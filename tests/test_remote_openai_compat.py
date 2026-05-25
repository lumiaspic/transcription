"""Tests for `backends.remote_openai_compat` — the generic OpenAI-compatible
remote backend used for OpenAI, Groq, and self-hosted Whisper servers.

HTTP is mocked by monkeypatching `httpx.post` so the tests stay hermetic
(no network, no extra mock library). Token storage goes through the shared
`fake_keyring` fixture from conftest, so we can also check the
"missing token" surface end-to-end.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from transcription import config as cfg
from transcription.backends.base import TranscriptionBackend
from transcription.backends.remote_openai_compat import (
    RemoteAPIConfigError,
    RemoteAPIRequestError,
    RemoteOpenAICompatBackend,
)
from transcription.pipeline.speakers import SpeakerProfile

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_config_and_token(fake_keyring: dict[tuple[str, str], str]) -> None:
    """Populate the minimum required state for the backend to construct
    and reach the request stage. Each test overrides only what it wants."""
    cfg.save_config(
        {
            **cfg.DEFAULT_CONFIG,
            "backend_mode": "remote_api",
            "remote_api_base_url": "https://api.example.com/v1",
            "remote_api_model": "whisper-large-v3",
            "remote_api_token_service": "remote_api",
        }
    )
    fake_keyring[(cfg.KEYRING_SERVICE, "remote_api")] = "sk-test"


def _verbose_json_payload(
    *,
    segments: list[dict[str, Any]] | None = None,
    language: str = "fr",
    duration: float = 5.0,
    text: str | None = None,
) -> dict[str, Any]:
    """Build a representative verbose_json body the way OpenAI / Groq return it."""
    body: dict[str, Any] = {
        "task": "transcribe",
        "language": language,
        "duration": duration,
    }
    if text is not None:
        body["text"] = text
    if segments is not None:
        body["segments"] = segments
    return body


def _audio_file(tmp_path: Path) -> Path:
    """Tiny placeholder audio file — content doesn't matter; the backend
    just multipart-uploads whatever bytes are on disk."""
    p = tmp_path / "clip.flac"
    p.write_bytes(b"fLaC\x00\x00\x00\x22")
    return p


class _FakeResponse:
    """Minimal stand-in for httpx.Response — only the attributes the backend
    reads. Avoids the full httpx.Response init dance."""

    def __init__(self, status_code: int, json_body: Any | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._json = json_body
        self.text = text if text else (str(json_body) if json_body is not None else "")

    def json(self) -> Any:
        if self._json is None:
            raise ValueError("no json body")
        return self._json


# ---------------------------------------------------------------------------
# Construction-time config validation
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_missing_base_url_raises_with_actionable_message(
        self,
        isolated_config_dir,  # noqa: ARG002
        fake_keyring,  # noqa: ARG002
    ) -> None:
        cfg.save_config({**cfg.DEFAULT_CONFIG, "backend_mode": "remote_api"})

        with pytest.raises(RemoteAPIConfigError) as exc_info:
            RemoteOpenAICompatBackend()

        # User must learn HOW to fix this, not just THAT it's broken.
        assert "remote_api_base_url" in str(exc_info.value)
        assert "config set" in str(exc_info.value)

    def test_missing_model_raises_with_actionable_message(
        self,
        isolated_config_dir,  # noqa: ARG002
        fake_keyring,  # noqa: ARG002
    ) -> None:
        cfg.save_config(
            {
                **cfg.DEFAULT_CONFIG,
                "backend_mode": "remote_api",
                "remote_api_base_url": "https://api.example.com/v1",
            }
        )

        with pytest.raises(RemoteAPIConfigError) as exc_info:
            RemoteOpenAICompatBackend()

        assert "remote_api_model" in str(exc_info.value)

    def test_trailing_slash_in_base_url_is_stripped(
        self,
        isolated_config_dir,  # noqa: ARG002
        fake_keyring,  # noqa: ARG002
    ) -> None:
        # Users will paste URLs both with and without a trailing slash —
        # normalise once at construction so the request path is predictable.
        cfg.save_config(
            {
                **cfg.DEFAULT_CONFIG,
                "backend_mode": "remote_api",
                "remote_api_base_url": "https://api.example.com/v1/",
                "remote_api_model": "whisper-1",
            }
        )

        backend = RemoteOpenAICompatBackend()

        assert backend.base_url == "https://api.example.com/v1"

    def test_explicit_args_override_config(
        self,
        isolated_config_dir,  # noqa: ARG002
        fake_keyring,  # noqa: ARG002
    ) -> None:
        # Useful for tests and for the CLI to bypass persisted config.
        cfg.save_config(
            {
                **cfg.DEFAULT_CONFIG,
                "backend_mode": "remote_api",
                "remote_api_base_url": "https://cfg.example.com/v1",
                "remote_api_model": "cfg-model",
            }
        )

        backend = RemoteOpenAICompatBackend(
            model="arg-model",
            base_url="https://arg.example.com/v1",
            token_service="arg-service",
            timeout_seconds=42.0,
        )

        assert backend.model_name == "arg-model"
        assert backend.base_url == "https://arg.example.com/v1"
        assert backend.token_service == "arg-service"
        assert backend.timeout_seconds == 42.0

    def test_implements_transcription_backend_interface(
        self,
        isolated_config_dir,  # noqa: ARG002
        fake_keyring: dict[tuple[str, str], str],
    ) -> None:
        _seed_config_and_token(fake_keyring)

        backend = RemoteOpenAICompatBackend()

        assert isinstance(backend, TranscriptionBackend)
        assert backend.name == "remote_openai_compat"


# ---------------------------------------------------------------------------
# Token resolution
# ---------------------------------------------------------------------------


class TestTokenResolution:
    def test_missing_token_raises_pointing_at_keyring_service(
        self,
        isolated_config_dir,  # noqa: ARG002
        fake_keyring,  # noqa: ARG002
    ) -> None:
        # Construction succeeds — token is only required at request time so
        # the doctor can still construct the backend to inspect it.
        cfg.save_config(
            {
                **cfg.DEFAULT_CONFIG,
                "backend_mode": "remote_api",
                "remote_api_base_url": "https://api.example.com/v1",
                "remote_api_model": "whisper-1",
                "remote_api_token_service": "my-custom-slot",
            }
        )
        backend = RemoteOpenAICompatBackend()

        with pytest.raises(RemoteAPIConfigError) as exc_info:
            backend._token()

        # Custom service name must appear so the user knows which slot to fill.
        assert "my-custom-slot" in str(exc_info.value)
        assert "set-token my-custom-slot" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Happy path — request shape + response parsing
# ---------------------------------------------------------------------------


class TestTranscribeHappyPath:
    def test_solo_request_shape_and_parsed_segments(
        self,
        tmp_path: Path,
        isolated_config_dir,  # noqa: ARG002
        fake_keyring: dict[tuple[str, str], str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _seed_config_and_token(fake_keyring)
        audio = _audio_file(tmp_path)

        seen: dict[str, Any] = {}

        def fake_post(url: str, **kwargs: Any) -> _FakeResponse:
            seen["url"] = url
            seen["headers"] = kwargs.get("headers")
            seen["data"] = kwargs.get("data")
            seen["files"] = kwargs.get("files")
            seen["timeout"] = kwargs.get("timeout")
            return _FakeResponse(
                200,
                _verbose_json_payload(
                    segments=[
                        {"start": 0.0, "end": 2.5, "text": " Bonjour."},
                        {"start": 3.0, "end": 5.2, "text": "Comment ça va ?"},
                    ],
                ),
            )

        monkeypatch.setattr(httpx, "post", fake_post)

        backend = RemoteOpenAICompatBackend()
        result = backend.transcribe(audio, profile=SpeakerProfile.SOLO, language="fr")

        # Request shape — what providers actually expect
        assert seen["url"] == "https://api.example.com/v1/audio/transcriptions"
        assert seen["headers"] == {"Authorization": "Bearer sk-test"}
        assert seen["data"] == {
            "model": "whisper-large-v3",
            "response_format": "verbose_json",
            "language": "fr",
        }
        # Multipart file: tuple of (filename, fileobj, content_type)
        assert "file" in seen["files"]
        filename, _fp, content_type = seen["files"]["file"]
        assert filename == "clip.flac"
        assert content_type == "application/octet-stream"
        assert seen["timeout"] == 600.0  # default

        # Parsed result
        assert result.language == "fr"
        assert result.duration == 5.0
        assert result.backend == "remote_openai_compat"
        assert result.model == "whisper-large-v3"
        assert result.profile == SpeakerProfile.SOLO
        assert [s.text for s in result.segments] == ["Bonjour.", "Comment ça va ?"]
        # Leading whitespace from Whisper is stripped
        assert result.segments[0].text == "Bonjour."
        # SOLO → all segments labelled SPEAKER_00
        assert {s.speaker for s in result.segments} == {"SPEAKER_00"}
        assert result.meta["base_url"] == "https://api.example.com/v1"
        assert result.meta["diarization_skipped"] is False

    def test_language_omitted_when_not_provided(
        self,
        tmp_path: Path,
        isolated_config_dir,  # noqa: ARG002
        fake_keyring: dict[tuple[str, str], str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Auto-detection path: caller passes no language, so the form must
        # NOT include a `language` field (otherwise we'd force whatever
        # default the provider has).
        _seed_config_and_token(fake_keyring)
        audio = _audio_file(tmp_path)
        seen: dict[str, Any] = {}

        def fake_post(url: str, **kwargs: Any) -> _FakeResponse:
            seen["data"] = kwargs.get("data")
            return _FakeResponse(200, _verbose_json_payload(segments=[]))

        monkeypatch.setattr(httpx, "post", fake_post)

        RemoteOpenAICompatBackend().transcribe(audio, profile=SpeakerProfile.SOLO)

        assert "language" not in seen["data"]

    def test_multi_profile_falls_back_to_single_speaker_with_warning(
        self,
        tmp_path: Path,
        isolated_config_dir,  # noqa: ARG002
        fake_keyring: dict[tuple[str, str], str],
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # No diarization on OpenAI-compat surface → all segments collapse to
        # SPEAKER_00 and the warning is loud enough to spot in logs.
        _seed_config_and_token(fake_keyring)
        audio = _audio_file(tmp_path)

        def fake_post(url: str, **kwargs: Any) -> _FakeResponse:  # noqa: ARG001
            return _FakeResponse(
                200,
                _verbose_json_payload(
                    segments=[
                        {"start": 0.0, "end": 1.0, "text": "one"},
                        {"start": 1.0, "end": 2.0, "text": "two"},
                    ],
                ),
            )

        monkeypatch.setattr(httpx, "post", fake_post)

        with caplog.at_level("WARNING"):
            result = RemoteOpenAICompatBackend().transcribe(audio, profile=SpeakerProfile.MULTI)

        assert {s.speaker for s in result.segments} == {"SPEAKER_00"}
        assert result.meta["diarization_skipped"] is True
        assert any("no diarization" in rec.message for rec in caplog.records)


class TestTranscribeResponseEdgeCases:
    def test_response_with_only_flat_text_synthesizes_single_segment(
        self,
        tmp_path: Path,
        isolated_config_dir,  # noqa: ARG002
        fake_keyring: dict[tuple[str, str], str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Some self-hosted servers honor `response_format=verbose_json` but
        # still omit `segments`. Don't lose the transcript — synthesize one
        # spanning the whole duration so downstream merge still works.
        _seed_config_and_token(fake_keyring)
        audio = _audio_file(tmp_path)

        def fake_post(url: str, **kwargs: Any) -> _FakeResponse:  # noqa: ARG001
            return _FakeResponse(
                200,
                _verbose_json_payload(text="full transcript", duration=12.5, segments=None),
            )

        monkeypatch.setattr(httpx, "post", fake_post)

        result = RemoteOpenAICompatBackend().transcribe(audio, profile=SpeakerProfile.SOLO)

        assert len(result.segments) == 1
        assert result.segments[0].text == "full transcript"
        assert result.segments[0].start == 0.0
        assert result.segments[0].end == 12.5

    def test_empty_response_yields_zero_segments_no_crash(
        self,
        tmp_path: Path,
        isolated_config_dir,  # noqa: ARG002
        fake_keyring: dict[tuple[str, str], str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Silence in / silence out — the worker should still mark the job
        # done rather than crash on a normal-but-empty result.
        _seed_config_and_token(fake_keyring)
        audio = _audio_file(tmp_path)

        def fake_post(url: str, **kwargs: Any) -> _FakeResponse:  # noqa: ARG001
            return _FakeResponse(200, _verbose_json_payload(segments=[], duration=0.0))

        monkeypatch.setattr(httpx, "post", fake_post)

        result = RemoteOpenAICompatBackend().transcribe(audio, profile=SpeakerProfile.SOLO)

        assert result.segments == []


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestTranscribeErrorPaths:
    def test_http_error_status_is_wrapped_with_body(
        self,
        tmp_path: Path,
        isolated_config_dir,  # noqa: ARG002
        fake_keyring: dict[tuple[str, str], str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _seed_config_and_token(fake_keyring)
        audio = _audio_file(tmp_path)

        def fake_post(url: str, **kwargs: Any) -> _FakeResponse:  # noqa: ARG001
            return _FakeResponse(
                401, json_body=None, text='{"error":{"message":"Invalid API key"}}'
            )

        monkeypatch.setattr(httpx, "post", fake_post)

        with pytest.raises(RemoteAPIRequestError) as exc_info:
            RemoteOpenAICompatBackend().transcribe(audio, profile=SpeakerProfile.SOLO)

        # The user sees both the status and the server's own message.
        msg = str(exc_info.value)
        assert "401" in msg
        assert "Invalid API key" in msg

    def test_transport_error_is_wrapped(
        self,
        tmp_path: Path,
        isolated_config_dir,  # noqa: ARG002
        fake_keyring: dict[tuple[str, str], str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Connection refused / DNS / read timeout — anything httpx raises
        # at transport level becomes a RemoteAPIRequestError so callers don't
        # have to import httpx to handle it.
        _seed_config_and_token(fake_keyring)
        audio = _audio_file(tmp_path)

        def fake_post(url: str, **kwargs: Any) -> _FakeResponse:  # noqa: ARG001
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(httpx, "post", fake_post)

        with pytest.raises(RemoteAPIRequestError) as exc_info:
            RemoteOpenAICompatBackend().transcribe(audio, profile=SpeakerProfile.SOLO)

        assert "connection refused" in str(exc_info.value)

    def test_non_json_body_on_success_is_wrapped(
        self,
        tmp_path: Path,
        isolated_config_dir,  # noqa: ARG002
        fake_keyring: dict[tuple[str, str], str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Misconfigured proxy returns HTML on 200. We don't want a cryptic
        # JSONDecodeError leaking up — wrap it with the body snippet so the
        # user can see what came back.
        _seed_config_and_token(fake_keyring)
        audio = _audio_file(tmp_path)

        def fake_post(url: str, **kwargs: Any) -> _FakeResponse:  # noqa: ARG001
            return _FakeResponse(200, json_body=None, text="<html>404 from proxy</html>")

        monkeypatch.setattr(httpx, "post", fake_post)

        with pytest.raises(RemoteAPIRequestError) as exc_info:
            RemoteOpenAICompatBackend().transcribe(audio, profile=SpeakerProfile.SOLO)

        assert "non-JSON" in str(exc_info.value)
        assert "404 from proxy" in str(exc_info.value)

    def test_missing_token_at_transcribe_time_raises_config_error(
        self,
        tmp_path: Path,
        isolated_config_dir,  # noqa: ARG002
        fake_keyring,  # noqa: ARG002
    ) -> None:
        # No token in keyring, but valid base_url + model → construction OK,
        # but the first transcribe call must fail loudly (and BEFORE the HTTP
        # call, so no leaked request).
        cfg.save_config(
            {
                **cfg.DEFAULT_CONFIG,
                "backend_mode": "remote_api",
                "remote_api_base_url": "https://api.example.com/v1",
                "remote_api_model": "whisper-1",
            }
        )
        backend = RemoteOpenAICompatBackend()

        with pytest.raises(RemoteAPIConfigError):
            backend.transcribe(_audio_file(tmp_path), profile=SpeakerProfile.SOLO)
