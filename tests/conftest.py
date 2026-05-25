"""Shared pytest fixtures.

Lives at the root of `tests/` so it's auto-discovered by all test modules
without needing an explicit import (pytest convention).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from transcription.backends.base import (
    DiarizationUnavailable,
    Segment,
    TranscriptionBackend,
    TranscriptResult,
)
from transcription.pipeline.speakers import SpeakerProfile

# ---------------------------------------------------------------------------
# TranscriptResult / Segment helpers
# ---------------------------------------------------------------------------


def _make_segment(start: float, end: float, text: str, speaker: str | None = None) -> Segment:
    return Segment(start=start, end=end, text=text, speaker=speaker)


@pytest.fixture
def mic_result() -> TranscriptResult:
    """A typical SOLO mic transcript with 2 short segments, no speaker labels."""
    return TranscriptResult(
        language="fr",
        segments=[
            _make_segment(0.0, 2.5, "Bonjour."),
            _make_segment(3.0, 5.2, "Comment ça va ?"),
        ],
        duration=5.2,
        backend="test",
        model="tiny",
        profile=SpeakerProfile.SOLO,
        track="mic",
    )


@pytest.fixture
def system_result() -> TranscriptResult:
    """A MULTI system transcript with 2 diarized speakers."""
    return TranscriptResult(
        language="fr",
        segments=[
            _make_segment(1.0, 2.0, "Salut.", speaker="SPEAKER_00"),
            _make_segment(2.5, 4.0, "Salut, ça va ?", speaker="SPEAKER_01"),
            _make_segment(4.5, 5.0, "Oui.", speaker="SPEAKER_00"),
        ],
        duration=5.0,
        backend="test",
        model="tiny",
        profile=SpeakerProfile.MULTI,
        track="system",
    )


# ---------------------------------------------------------------------------
# FakeBackend — stand-in for the real WhisperX backend in tests.
# Returns a canned TranscriptResult and lets tests assert how it was called.
# Optionally raises DiarizationUnavailable on the first MULTI call to
# exercise the SOLO fallback path in `run_transcription`.
# ---------------------------------------------------------------------------


class FakeBackend(TranscriptionBackend):
    name = "fake"

    def __init__(
        self,
        *,
        fail_diarization_once: bool = False,
        duration: float = 1.0,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._fail_once = fail_diarization_once
        self._duration = duration

    def transcribe(
        self,
        audio_path: Path,
        profile: SpeakerProfile,
        language: str | None = None,
    ) -> TranscriptResult:
        self.calls.append({"audio_path": audio_path, "profile": profile, "language": language})

        if self._fail_once and profile == SpeakerProfile.MULTI:
            self._fail_once = False  # only fail the first time
            raise DiarizationUnavailable("no HF token (simulated)")

        return TranscriptResult(
            language=language or "en",
            segments=[
                Segment(start=0.0, end=self._duration, text=f"hello from {audio_path.stem}"),
            ],
            duration=self._duration,
            backend=self.name,
            model="fake-model",
            profile=profile,
        )


@pytest.fixture
def fake_backend() -> FakeBackend:
    return FakeBackend()


# ---------------------------------------------------------------------------
# FakeDualRecorder — stand-in for audio.DualRecorder for tests of code that
# constructs a recorder (e.g. the `record` CLI command, the GUI Recording
# card). Records constructor kwargs and start/stop calls; never touches
# audio devices or the filesystem.
# ---------------------------------------------------------------------------


class FakeDualRecorder:
    """Mirrors DualRecorder's constructor surface without doing any I/O."""

    # Set by the fixture before each test so assertions don't leak between
    # tests if a previous one bailed before invoking the recorder.
    last_instance: FakeDualRecorder | None = None

    def __init__(
        self,
        out_dir: Path,
        mic_name: str | None = None,
        speaker_name: str | None = None,
        *,
        sample_rate: int = 16_000,
        format: str = "flac",
    ) -> None:
        self.out_dir = out_dir
        self.mic_name = mic_name
        self.speaker_name = speaker_name
        self.sample_rate = sample_rate
        self.format = format
        self.started = False
        self.stopped = False
        type(self).last_instance = self

    def start(self) -> None:
        self.started = True

    def stop(self, timeout: float = 5.0) -> None:  # noqa: ARG002
        self.stopped = True


@pytest.fixture
def fake_dual_recorder(monkeypatch: pytest.MonkeyPatch) -> type[FakeDualRecorder]:
    """Replace `cli.DualRecorder` with FakeDualRecorder and reset its state.

    Yields the class so tests can read `.last_instance` after invoking a
    command. Also patches `cli.time.sleep` to a no-op so the record loop's
    `time.sleep(duration)` returns instantly.
    """
    from transcription import cli as cli_mod

    FakeDualRecorder.last_instance = None
    monkeypatch.setattr(cli_mod, "DualRecorder", FakeDualRecorder)
    monkeypatch.setattr(cli_mod.time, "sleep", lambda _seconds: None)
    return FakeDualRecorder


# ---------------------------------------------------------------------------
# Environment isolation — point the app's config dir at a per-test tmp dir.
#
# `paths.config_dir()` is platform-aware: APPDATA on Windows,
# ~/Library/Application Support on macOS, ~/.config elsewhere.
# We redirect both APPDATA and HOME so no test ever touches the user's real
# config, regardless of which platform CI runs on.
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect `config_dir()` to a fresh tmp dir for this test."""
    fake_home = tmp_path / "home"
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
    if sys.platform == "win32":
        fake_appdata = fake_home / "AppData" / "Roaming"
        monkeypatch.setenv("APPDATA", str(fake_appdata))
        return fake_appdata / "transcription"
    elif sys.platform == "darwin":
        return fake_home / "Library" / "Application Support" / "transcription"
    else:
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        return fake_home / ".config" / "transcription"


# ---------------------------------------------------------------------------
# In-memory keyring — replaces the OS keyring (Credential Manager / Keychain)
# for tests touching tokens. Avoids both pollution of the user's keyring AND
# the CI box having no keyring backend at all.
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, str], str]:
    """Replaces `keyring.{set,get,delete}_password` with a dict-backed stub.

    Yields the underlying dict so tests can inspect or seed it directly.
    """
    import keyring
    import keyring.errors

    store: dict[tuple[str, str], str] = {}

    def _set(service: str, username: str, password: str) -> None:
        store[(service, username)] = password

    def _get(service: str, username: str) -> str | None:
        return store.get((service, username))

    def _delete(service: str, username: str) -> None:
        if (service, username) not in store:
            raise keyring.errors.PasswordDeleteError("not found")
        del store[(service, username)]

    monkeypatch.setattr(keyring, "set_password", _set)
    monkeypatch.setattr(keyring, "get_password", _get)
    monkeypatch.setattr(keyring, "delete_password", _delete)
    return store
