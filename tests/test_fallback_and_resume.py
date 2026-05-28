"""Tests for the fallback chain + per-track resume in `pipeline.transcribe`.

The chain is driven by RecoverableRemoteAPIError: backends raising one of
those let the orchestrator advance to the next entry for the same track.
Terminal errors (e.g. AuthError) and unrelated exceptions bubble up.

Per-track resume reads `<track>.json` from disk and skips the backend call
entirely when the JSON is newer than its source audio.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import FakeBackend
from transcription.backends.base import (
    Segment,
    TranscriptionBackend,
    TranscriptResult,
)
from transcription.backends.remote_openai_compat import (
    AuthError,
    PayloadTooLarge,
    QuotaExceeded,
    RateLimited,
    _classify_http_error,
)
from transcription.pipeline.speakers import SpeakerProfile
from transcription.pipeline.transcribe import run_transcription

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _touch_track(rec_dir: Path, track: str, ext: str = "flac") -> Path:
    rec_dir.mkdir(parents=True, exist_ok=True)
    p = rec_dir / f"{track}.{ext}"
    p.write_bytes(b"")
    return p


class _FailingBackend(TranscriptionBackend):
    """Always raises the given exception. Records how many times it ran."""

    def __init__(self, exc: Exception, name: str = "broken") -> None:
        self.name = name
        self.exc = exc
        self.calls = 0

    def transcribe(
        self,
        audio_path: Path,
        profile: SpeakerProfile,
        language: str | None = None,
    ) -> TranscriptResult:
        self.calls += 1
        raise self.exc


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------


class TestClassifyHttpError:
    def test_413_maps_to_payload_too_large(self) -> None:
        # 413 is the universal "your file is too big" signal — Groq, OpenAI,
        # whisper.cpp's server all agree.
        assert isinstance(_classify_http_error(413, "too big"), PayloadTooLarge)

    def test_429_with_insufficient_quota_maps_to_quota_exceeded(self) -> None:
        # OpenAI returns 429 for both transient rate-limits AND account
        # out-of-credits — the body's error code is the only thing that tells
        # them apart, and waiting won't fix the second one.
        body = '{"error": {"code": "insufficient_quota"}}'
        assert isinstance(_classify_http_error(429, body), QuotaExceeded)

    def test_plain_429_maps_to_rate_limited(self) -> None:
        assert isinstance(_classify_http_error(429, "slow down"), RateLimited)

    def test_401_maps_to_auth_error(self) -> None:
        assert isinstance(_classify_http_error(401, "bad key"), AuthError)

    def test_unknown_status_falls_back_to_base_error(self) -> None:
        # A 5xx (or any unmapped status) still surfaces with the raw body so
        # users see what went wrong, just under the generic base class.
        from transcription.backends.remote_openai_compat import RemoteAPIRequestError

        err = _classify_http_error(503, "upstream down")

        assert isinstance(err, RemoteAPIRequestError)
        assert not isinstance(err, AuthError)
        assert "upstream down" in str(err)


# ---------------------------------------------------------------------------
# Fallback chain inside run_transcription
# ---------------------------------------------------------------------------


class TestFallbackChain:
    def test_recoverable_error_advances_to_next_backend(self, tmp_path: Path) -> None:
        # Groq says "payload too large", the next entry (a local backend)
        # picks the track up and succeeds.
        rec_dir = tmp_path / "rec"
        _touch_track(rec_dir, "mic")

        primary = _FailingBackend(PayloadTooLarge("413"), name="groq")
        secondary = FakeBackend()

        results = run_transcription(rec_dir, [primary, secondary])

        assert primary.calls == 1
        assert len(secondary.calls) == 1
        # The result is attributed to the backend that actually produced it.
        assert results[0].backend == "fake"

    def test_terminal_error_does_not_fall_through(self, tmp_path: Path) -> None:
        # AuthError is local to one backend's config — trying the next
        # backend (which has its own token) might paper over a real bug, so
        # we surface immediately instead.
        rec_dir = tmp_path / "rec"
        _touch_track(rec_dir, "mic")

        primary = _FailingBackend(AuthError("401"), name="groq")
        secondary = FakeBackend()

        with pytest.raises(AuthError):
            run_transcription(rec_dir, [primary, secondary])

        # The chain stopped at the first backend.
        assert primary.calls == 1
        assert secondary.calls == []

    def test_exhausted_chain_raises_last_recoverable(self, tmp_path: Path) -> None:
        # Every backend in the chain hits a recoverable wall. The final
        # exception is the most actionable — surface it instead of the first.
        rec_dir = tmp_path / "rec"
        _touch_track(rec_dir, "mic")

        a = _FailingBackend(PayloadTooLarge("413 groq"), name="groq")
        b = _FailingBackend(QuotaExceeded("429 openai"), name="openai")

        with pytest.raises(QuotaExceeded, match="429 openai"):
            run_transcription(rec_dir, [a, b])

    def test_each_track_independently_falls_back(self, tmp_path: Path) -> None:
        # The chain advances per-track, not per-recording. A primary that
        # only fails on the larger track still handles the smaller one.
        rec_dir = tmp_path / "rec"
        _touch_track(rec_dir, "mic")
        _touch_track(rec_dir, "system")

        class FailOnSystem(TranscriptionBackend):
            name = "primary"

            def __init__(self) -> None:
                self.calls: list[str] = []

            def transcribe(
                self,
                audio_path: Path,
                profile: SpeakerProfile,
                language: str | None = None,
            ) -> TranscriptResult:
                self.calls.append(audio_path.name)
                if "system" in audio_path.name:
                    raise RateLimited("429")
                return TranscriptResult(
                    language="fr",
                    segments=[Segment(0.0, 1.0, "ok")],
                    duration=1.0,
                    backend=self.name,
                    model="m",
                    profile=profile,
                )

        primary = FailOnSystem()
        secondary = FakeBackend()

        results = run_transcription(rec_dir, [primary, secondary])

        # Mic stayed on the primary, system fell through to the secondary.
        by_track = {r.track: r.backend for r in results}
        assert by_track == {"mic": "primary", "system": "fake"}

    def test_progress_callback_announces_recoverable_failure(self, tmp_path: Path) -> None:
        # The user sees an inline note when one chain entry hands off — vital
        # so a long-running session isn't silent on the "actually it's groq
        # that failed and openai picked up" transition.
        rec_dir = tmp_path / "rec"
        _touch_track(rec_dir, "mic")

        primary = _FailingBackend(PayloadTooLarge("413"), name="groq")
        secondary = FakeBackend()
        messages: list[str] = []

        run_transcription(rec_dir, [primary, secondary], progress=messages.append)

        assert any("groq failed" in m and "PayloadTooLarge" in m for m in messages)

    def test_meta_records_per_track_backend(self, tmp_path: Path) -> None:
        rec_dir = tmp_path / "rec"
        _touch_track(rec_dir, "mic")
        _touch_track(rec_dir, "system")

        primary = _FailingBackend(PayloadTooLarge("413"), name="groq")
        secondary = FakeBackend()

        run_transcription(rec_dir, [primary, secondary])

        meta = json.loads((rec_dir / "meta.json").read_text(encoding="utf-8"))
        # The audit trail: which backend ended up handling each track.
        assert meta["per_track_backend"] == {"mic": "fake", "system": "fake"}


# ---------------------------------------------------------------------------
# Per-track resume
# ---------------------------------------------------------------------------


class TestResume:
    def test_skips_track_when_json_is_newer_than_audio(self, tmp_path: Path) -> None:
        # Mimic the post-crash recovery scenario from the issue: mic was
        # already transcribed before the rate-limit hit on system. Re-running
        # must not redo mic.
        rec_dir = tmp_path / "rec"
        mic_audio = _touch_track(rec_dir, "mic")
        _touch_track(rec_dir, "system")
        existing = {
            "language": "fr",
            "duration": 12.3,
            "backend": "groq",
            "model": "whisper-large-v3-turbo",
            "profile": SpeakerProfile.SOLO.value,
            "track": "mic",
            "segments": [{"start": 0.0, "end": 1.0, "text": "cached", "speaker": "SPEAKER_00"}],
            "meta": {},
        }
        json_path = rec_dir / "mic.json"
        json_path.write_text(json.dumps(existing), encoding="utf-8")
        future = mic_audio.stat().st_mtime + 10
        os.utime(json_path, (future, future))

        backend = FakeBackend()
        results = run_transcription(rec_dir, backend)

        # Only the system track triggered the backend.
        assert len(backend.calls) == 1
        assert backend.calls[0]["audio_path"].name.startswith("system")
        # Mic result came from the cached JSON, not the backend.
        mic_result = next(r for r in results if r.track == "mic")
        assert mic_result.backend == "groq"
        assert mic_result.segments[0].text == "cached"

    def test_force_re_transcribes_even_when_json_present(self, tmp_path: Path) -> None:
        rec_dir = tmp_path / "rec"
        mic_audio = _touch_track(rec_dir, "mic")
        json_path = rec_dir / "mic.json"
        json_path.write_text(
            json.dumps(
                {
                    "language": "fr",
                    "duration": 1.0,
                    "backend": "groq",
                    "model": "x",
                    "profile": SpeakerProfile.SOLO.value,
                    "track": "mic",
                    "segments": [],
                    "meta": {},
                }
            ),
            encoding="utf-8",
        )
        future = mic_audio.stat().st_mtime + 10
        os.utime(json_path, (future, future))

        backend = FakeBackend()
        run_transcription(rec_dir, backend, force=True)

        assert len(backend.calls) == 1

    def test_progress_callback_announces_skip(self, tmp_path: Path) -> None:
        rec_dir = tmp_path / "rec"
        mic_audio = _touch_track(rec_dir, "mic")
        json_path = rec_dir / "mic.json"
        json_path.write_text(
            json.dumps(
                {
                    "language": "fr",
                    "duration": 1.0,
                    "backend": "groq",
                    "model": "x",
                    "profile": SpeakerProfile.SOLO.value,
                    "track": "mic",
                    "segments": [],
                    "meta": {},
                }
            ),
            encoding="utf-8",
        )
        future = mic_audio.stat().st_mtime + 10
        os.utime(json_path, (future, future))

        messages: list[str] = []
        run_transcription(rec_dir, FakeBackend(), progress=messages.append)

        assert any("Skipping mic" in m and "--force" in m for m in messages)

    def test_malformed_json_triggers_re_transcribe(self, tmp_path: Path) -> None:
        # A truncated / corrupt cache (e.g. crash mid-write) must NOT poison
        # the resume — the orchestrator falls through to the backend instead.
        rec_dir = tmp_path / "rec"
        mic_audio = _touch_track(rec_dir, "mic")
        json_path = rec_dir / "mic.json"
        json_path.write_text("{not valid json", encoding="utf-8")
        future = mic_audio.stat().st_mtime + 10
        os.utime(json_path, (future, future))

        backend = FakeBackend()
        run_transcription(rec_dir, backend)

        assert len(backend.calls) == 1

    def test_stale_json_triggers_re_transcribe(self, tmp_path: Path) -> None:
        # The user re-recorded over an old mic.flac; mtime of audio is now
        # newer than the cached transcript. The cache must lose.
        rec_dir = tmp_path / "rec"
        mic_audio = _touch_track(rec_dir, "mic")
        json_path = rec_dir / "mic.json"
        json_path.write_text(
            json.dumps(
                {
                    "language": "fr",
                    "duration": 1.0,
                    "backend": "old",
                    "model": "x",
                    "profile": SpeakerProfile.SOLO.value,
                    "track": "mic",
                    "segments": [],
                    "meta": {},
                }
            ),
            encoding="utf-8",
        )
        past = mic_audio.stat().st_mtime - 10
        os.utime(json_path, (past, past))

        backend = FakeBackend()
        run_transcription(rec_dir, backend)

        assert len(backend.calls) == 1


# ---------------------------------------------------------------------------
# Backwards compat: single backend still accepted
# ---------------------------------------------------------------------------


class TestBackwardsCompat:
    def test_single_backend_argument_still_works(self, tmp_path: Path) -> None:
        # Old callers passing a single backend instance keep working — the
        # orchestrator treats it as a one-entry chain.
        rec_dir = tmp_path / "rec"
        _touch_track(rec_dir, "mic")

        backend = FakeBackend()
        results = run_transcription(rec_dir, backend)

        assert len(results) == 1
        assert results[0].backend == "fake"

    def test_empty_chain_raises(self, tmp_path: Path) -> None:
        rec_dir = tmp_path / "rec"
        _touch_track(rec_dir, "mic")

        with pytest.raises(ValueError, match="empty"):
            run_transcription(rec_dir, [])


# ---------------------------------------------------------------------------
# Factory chain
# ---------------------------------------------------------------------------


class TestGetBackendChain:
    def test_default_chain_is_single_backend_mode(
        self,
        isolated_config_dir: Path,  # noqa: ARG002
    ) -> None:
        # When no `backend_fallback_chain` is set, behavior is identical to
        # the old single-backend factory (same backend_mode is honored).
        from transcription import config as cfg
        from transcription.backends.factory import get_backend_chain
        from transcription.backends.whisperx_local import WhisperXLocalBackend

        cfg.save_config({**cfg.DEFAULT_CONFIG, "backend_mode": "local_cpu"})

        chain = get_backend_chain()

        assert len(chain) == 1
        assert isinstance(chain[0], WhisperXLocalBackend)

    def test_non_list_chain_raises(self, isolated_config_dir: Path) -> None:  # noqa: ARG002
        # Guard against a user typo like `backend_fallback_chain = "remote_api"`
        # (a string, not a list) — fail fast instead of iterating characters.
        from transcription import config as cfg
        from transcription.backends.factory import get_backend_chain

        cfg.save_config({**cfg.DEFAULT_CONFIG, "backend_fallback_chain": "remote_api"})

        with pytest.raises(ValueError, match="non-empty list"):
            get_backend_chain()

    def test_chain_constructs_each_named_entry(
        self,
        isolated_config_dir: Path,  # noqa: ARG002
        fake_keyring: dict[tuple[str, str], str],  # noqa: ARG002
    ) -> None:
        # A chain mixing a custom remote name + the legacy "remote_api" slot
        # + the local fallback. Each entry must construct without surprise.
        from transcription import config as cfg
        from transcription.backends.factory import get_backend_chain
        from transcription.backends.remote_openai_compat import RemoteOpenAICompatBackend
        from transcription.backends.whisperx_local import WhisperXLocalBackend

        cfg.save_config(
            {
                **cfg.DEFAULT_CONFIG,
                "backend_fallback_chain": ["groq_primary", "remote_api", "local_cpu"],
                # Legacy remote_api slot
                "remote_api_base_url": "https://api.groq.com/openai/v1",
                "remote_api_model": "whisper-large-v3-turbo",
                # Nested backends table for the named entry
                "backends": {
                    "groq_primary": {
                        "base_url": "https://api.groq.com/openai/v1",
                        "model": "whisper-large-v3-turbo",
                    }
                },
            }
        )

        chain = get_backend_chain()

        assert len(chain) == 3
        assert isinstance(chain[0], RemoteOpenAICompatBackend)
        assert chain[0].name == "groq_primary"
        assert isinstance(chain[1], RemoteOpenAICompatBackend)
        assert chain[1].name == "remote_api"
        assert isinstance(chain[2], WhisperXLocalBackend)


# ---------------------------------------------------------------------------
# Config: resolve_remote_backend_config
# ---------------------------------------------------------------------------


class TestResolveRemoteBackendConfig:
    def test_legacy_flat_keys_resolve_remote_api(self) -> None:
        from transcription.config import resolve_remote_backend_config

        cfg = {
            "remote_api_base_url": "https://x/v1",
            "remote_api_model": "m",
            "remote_api_token_service": "svc",
            "remote_api_timeout_seconds": 30,
            "backends": {},
        }

        resolved = resolve_remote_backend_config("remote_api", cfg)

        assert resolved == {
            "base_url": "https://x/v1",
            "model": "m",
            "token_service": "svc",
            "timeout_seconds": 30,
        }

    def test_nested_table_wins_over_flat_keys(self) -> None:
        # When both layouts are present, the nested entry takes precedence
        # so users can migrate one key at a time.
        from transcription.config import resolve_remote_backend_config

        cfg = {
            "remote_api_base_url": "https://legacy/v1",
            "remote_api_model": "legacy",
            "backends": {
                "remote_api": {
                    "base_url": "https://nested/v1",
                    "model": "nested",
                }
            },
        }

        resolved = resolve_remote_backend_config("remote_api", cfg)

        assert resolved["base_url"] == "https://nested/v1"
        assert resolved["model"] == "nested"

    def test_custom_name_defaults_token_service_to_name(self) -> None:
        # Custom backends keep their token in a keyring slot named after them
        # unless the user specifies otherwise — this is what lets multiple
        # providers coexist without extra config.
        from transcription.config import resolve_remote_backend_config

        cfg: dict[str, Any] = {
            "backends": {"openai_fallback": {"base_url": "https://o/v1", "model": "w1"}}
        }

        resolved = resolve_remote_backend_config("openai_fallback", cfg)

        assert resolved["token_service"] == "openai_fallback"
