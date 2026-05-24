"""Tests for `pipeline.transcribe` — orchestration of a backend over tracks.

Uses the `FakeBackend` defined in conftest.py so we never invoke real
WhisperX/torch. Tests focus on:
  - track discovery (mic / system × flac / wav)
  - profile selection (SOLO mic, MULTI system, --no-diarize override)
  - the DiarizationUnavailable → SOLO fallback (regression for the bug
    found by ruff F821 in the first CI PR)
  - meta.json read/write
  - output files written (delegated to format.py / merge.py)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import FakeBackend
from transcription.pipeline.speakers import SpeakerProfile
from transcription.pipeline.transcribe import (
    KNOWN_TRACKS,
    TRACK_EXTS,
    find_track_file,
    run_transcription,
)

# ---------------------------------------------------------------------------
# Helpers: create empty placeholder audio files. The fake backend never reads
# them, so an empty file with the right name is enough.
# ---------------------------------------------------------------------------


def _touch_track(rec_dir: Path, track: str, ext: str = "flac") -> Path:
    rec_dir.mkdir(parents=True, exist_ok=True)
    p = rec_dir / f"{track}.{ext}"
    p.write_bytes(b"")
    return p


# ---------------------------------------------------------------------------
# find_track_file
# ---------------------------------------------------------------------------


class TestFindTrackFile:
    def test_prefers_flac_over_wav_when_both_exist(self, tmp_path: Path) -> None:
        # New recordings are FLAC, legacy were WAV. If both somehow exist
        # (e.g. user converted manually), the new format wins.
        _touch_track(tmp_path, "mic", ext="wav")
        _touch_track(tmp_path, "mic", ext="flac")

        assert find_track_file(tmp_path, "mic") == tmp_path / "mic.flac"

    def test_falls_back_to_wav_when_no_flac(self, tmp_path: Path) -> None:
        _touch_track(tmp_path, "mic", ext="wav")

        assert find_track_file(tmp_path, "mic") == tmp_path / "mic.wav"

    def test_returns_none_when_track_missing(self, tmp_path: Path) -> None:
        assert find_track_file(tmp_path, "system") is None

    def test_constants_stay_in_sync(self) -> None:
        # Guard against silent drift between the constants and the function.
        assert "flac" in TRACK_EXTS
        assert "mic" in KNOWN_TRACKS and "system" in KNOWN_TRACKS


# ---------------------------------------------------------------------------
# run_transcription
# ---------------------------------------------------------------------------


class TestRunTranscription:
    def test_raises_when_recording_dir_missing(
        self, tmp_path: Path, fake_backend: FakeBackend
    ) -> None:
        with pytest.raises(FileNotFoundError, match="Recording dir not found"):
            run_transcription(tmp_path / "does_not_exist", fake_backend)

    def test_raises_when_no_audio_tracks_present(
        self, tmp_path: Path, fake_backend: FakeBackend
    ) -> None:
        rec_dir = tmp_path / "rec"
        rec_dir.mkdir()
        # Dir exists but contains no mic.* or system.* file.

        with pytest.raises(FileNotFoundError, match=r"No \{mic\|system\}"):
            run_transcription(rec_dir, fake_backend)

    def test_calls_backend_once_per_present_track(
        self, tmp_path: Path, fake_backend: FakeBackend
    ) -> None:
        rec_dir = tmp_path / "rec"
        _touch_track(rec_dir, "mic")
        _touch_track(rec_dir, "system")

        results = run_transcription(rec_dir, fake_backend)

        assert len(results) == 2
        assert len(fake_backend.calls) == 2

    def test_assigns_solo_profile_to_mic_and_multi_to_system(
        self, tmp_path: Path, fake_backend: FakeBackend
    ) -> None:
        rec_dir = tmp_path / "rec"
        _touch_track(rec_dir, "mic")
        _touch_track(rec_dir, "system")

        run_transcription(rec_dir, fake_backend)

        # Calls happen in KNOWN_TRACKS order: mic first, system second.
        assert fake_backend.calls[0]["profile"] is SpeakerProfile.SOLO
        assert fake_backend.calls[1]["profile"] is SpeakerProfile.MULTI

    def test_diarize_false_downgrades_system_track_to_solo(
        self, tmp_path: Path, fake_backend: FakeBackend
    ) -> None:
        rec_dir = tmp_path / "rec"
        _touch_track(rec_dir, "system")

        run_transcription(rec_dir, fake_backend, diarize=False)

        assert fake_backend.calls[0]["profile"] is SpeakerProfile.SOLO

    def test_results_get_track_name_attached(
        self, tmp_path: Path, fake_backend: FakeBackend
    ) -> None:
        rec_dir = tmp_path / "rec"
        _touch_track(rec_dir, "mic")
        _touch_track(rec_dir, "system")

        results = run_transcription(rec_dir, fake_backend)

        # The backend doesn't know its track; the orchestrator must label it.
        assert {r.track for r in results} == {"mic", "system"}

    def test_writes_per_track_outputs_and_merged_markdown(
        self, tmp_path: Path, fake_backend: FakeBackend
    ) -> None:
        rec_dir = tmp_path / "rec"
        _touch_track(rec_dir, "mic")
        _touch_track(rec_dir, "system")

        run_transcription(rec_dir, fake_backend)

        # Per-track exports (delegated to export.format).
        for track in ("mic", "system"):
            for ext in ("txt", "srt", "json"):
                assert (rec_dir / f"{track}.{ext}").exists(), f"missing {track}.{ext}"
        # Merged markdown (delegated to export.merge).
        assert (rec_dir / "transcript.md").exists()

    def test_updates_meta_json_with_transcription_status(
        self, tmp_path: Path, fake_backend: FakeBackend
    ) -> None:
        rec_dir = tmp_path / "rec"
        _touch_track(rec_dir, "mic")
        # Pre-existing meta.json (e.g. written by `record` at capture time).
        (rec_dir / "meta.json").write_text(
            json.dumps({"id": "rec", "tracks": ["mic"]}), encoding="utf-8"
        )

        run_transcription(rec_dir, fake_backend)

        meta = json.loads((rec_dir / "meta.json").read_text(encoding="utf-8"))
        assert meta["transcribed"] is True
        assert meta["backend"] == "fake"
        assert meta["model"] == "fake-model"
        assert meta["transcribed_at"]
        # Original fields preserved.
        assert meta["id"] == "rec"
        assert meta["tracks"] == ["mic"]

    def test_creates_meta_json_when_absent(self, tmp_path: Path, fake_backend: FakeBackend) -> None:
        # No pre-existing meta.json at all — should still write one.
        rec_dir = tmp_path / "rec"
        _touch_track(rec_dir, "mic")

        run_transcription(rec_dir, fake_backend)

        assert (rec_dir / "meta.json").exists()

    def test_passes_language_through_to_backend(
        self, tmp_path: Path, fake_backend: FakeBackend
    ) -> None:
        rec_dir = tmp_path / "rec"
        _touch_track(rec_dir, "mic")

        run_transcription(rec_dir, fake_backend, language="fr")

        assert fake_backend.calls[0]["language"] == "fr"

    def test_progress_callback_invoked_at_least_once_per_track(
        self, tmp_path: Path, fake_backend: FakeBackend
    ) -> None:
        rec_dir = tmp_path / "rec"
        _touch_track(rec_dir, "mic")
        _touch_track(rec_dir, "system")
        messages: list[str] = []

        run_transcription(rec_dir, fake_backend, progress=messages.append)

        # At minimum: one "Transcribing X" per track + one summary per track.
        assert len(messages) >= 4
        assert any("Transcribing mic" in m for m in messages)
        assert any("Transcribing system" in m for m in messages)


class TestDiarizationFallback:
    """Specifically guards the bug found by ruff F821 in the first CI PR."""

    def test_diarization_unavailable_triggers_solo_retry_on_same_audio(
        self, tmp_path: Path
    ) -> None:
        backend = FakeBackend(fail_diarization_once=True)
        rec_dir = tmp_path / "rec"
        _touch_track(rec_dir, "system")

        messages: list[str] = []
        results = run_transcription(rec_dir, backend, progress=messages.append)

        # Two calls were made on the same audio_path: first MULTI (failed),
        # then SOLO (succeeded). Before the fix, the retry passed an undefined
        # `wav` name and raised NameError instead.
        assert len(backend.calls) == 2
        assert backend.calls[0]["audio_path"] == backend.calls[1]["audio_path"]
        assert backend.calls[0]["profile"] is SpeakerProfile.MULTI
        assert backend.calls[1]["profile"] is SpeakerProfile.SOLO
        # The returned result is the SOLO one.
        assert results[0].profile is SpeakerProfile.SOLO
        # A user-facing warning was surfaced through the progress callback.
        assert any("diarization unavailable" in m for m in messages)
