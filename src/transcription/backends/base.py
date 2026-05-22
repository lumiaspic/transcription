"""Abstract TranscriptionBackend interface.

Implementations live in sibling modules:
    - whisperx_local.py : local WhisperX (GPU or CPU)
    - remote_api.py     : future, HTTP client for Replicate / RunPod / self-hosted
    - faster_whisper_cpu.py : future, lighter CPU-only path
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from ..pipeline.speakers import SpeakerProfile


@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker: str | None = None  # e.g. "SPEAKER_00", None if no speaker info


@dataclass
class TranscriptResult:
    language: str
    segments: list[Segment]
    duration: float
    backend: str
    model: str
    profile: SpeakerProfile
    track: str = ""  # populated by caller, e.g. "mic" or "system"
    meta: dict = field(default_factory=dict)


class TranscriptionBackend(ABC):
    name: str  # short identifier, e.g. "whisperx_local"

    @abstractmethod
    def transcribe(
        self,
        audio_path: Path,
        profile: SpeakerProfile,
        language: str | None = None,
    ) -> TranscriptResult:
        """Transcribe an audio file.

        Args:
            audio_path: WAV/FLAC/etc. path.
            profile: SOLO skips diarization; MULTI runs it.
            language: ISO code (e.g. 'fr') or None to auto-detect.
        """


class DiarizationUnavailable(RuntimeError):
    """Raised when MULTI profile is requested but diarization can't run
    (no HF token, model download failed, etc.). Caller should warn and
    fall back to a single-speaker transcript."""
