"""Dual-track recorder: mic + system loopback, written as 2 separate audio files.

Streams to disk in small chunks - bounded memory regardless of recording length.

Format and sample rate are configurable (see config.recording_format and
config.recording_sample_rate). Defaults are Opus at 16 kHz: WhisperX and
pyannote both resample to 16 kHz internally, so anything higher is bytes
on disk that the pipeline immediately discards. Opus (in an Ogg container)
is lossy but transparent for speech at roughly a tenth of FLAC's size,
which keeps recordings small and well under remote-API upload caps. FLAC
(lossless) and WAV (raw PCM) stay available for archival needs.

Backward-compat: older recordings on disk as .flac / .wav remain readable
thanks to the format-agnostic track lookup in pipeline/transcribe.py.
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from . import devices

log = logging.getLogger(__name__)

# Defaults used when no override is passed and no config exists. Constructor
# arguments and the config keys take precedence over these.
DEFAULT_SAMPLE_RATE = 16_000
DEFAULT_FORMAT = "opus"

# User-facing format name -> (libsndfile container, subtype, file extension).
# Opus rides in an Ogg container; libsndfile streams it the same way as FLAC
# and WAV, so the chunked write loop below is format-agnostic. PCM subtypes
# only make sense for the lossless containers.
_FORMAT_SPECS: dict[str, tuple[str, str, str]] = {
    "opus": ("OGG", "OPUS", "opus"),
    "flac": ("FLAC", "PCM_16", "flac"),
    "wav": ("WAV", "PCM_16", "wav"),
}
SUPPORTED_FORMATS = tuple(_FORMAT_SPECS)

CHUNK_SECONDS = (
    0.1  # 100 ms blocks; small enough for responsive stop, big enough to avoid syscall thrash
)


def _driver_blocksize(chunk_frames: int) -> int | None:
    """Buffer size passed to soundcard.recorder().

    WASAPI (Windows) accepts arbitrary sizes, so we match it to our chunk_frames
    to keep the driver buffer aligned with our read loop.
    CoreAudio (macOS) caps blocksize at 512 frames; passing larger values raises
    TypeError. We pass None and let CoreAudio pick a device-appropriate value —
    record(numframes=chunk_frames) still aggregates to our 100ms target.
    """
    if sys.platform == "darwin":
        return None
    return chunk_frames


@dataclass
class TrackSpec:
    label: str  # "mic" or "system"
    # soundcard's recorder() returns a context manager yielding the recorder
    # object; soundcard has no type stubs, so the inner type is Any.
    recorder_cm: AbstractContextManager[Any]
    out_path: Path
    channels: int
    sample_rate: int
    sf_format: str  # libsndfile container: "OGG" / "FLAC" / "WAV"
    subtype: str  # libsndfile subtype: "OPUS" / "PCM_16"
    # Called after each captured chunk with a peak amplitude in [0.0, 1.0].
    # Used by the GUI to drive live level meters. No-op default so the spec
    # is still useful in tests that don't care about levels.
    on_level: Callable[[float], None] = field(default=lambda _v: None)


def _stream_track(spec: TrackSpec, stop: threading.Event) -> None:
    spec.out_path.parent.mkdir(parents=True, exist_ok=True)
    log.info(
        "[%s] starting -> %s (%d Hz, %s)",
        spec.label,
        spec.out_path,
        spec.sample_rate,
        spec.sf_format,
    )
    chunk_frames = int(spec.sample_rate * CHUNK_SECONDS)
    try:
        with (
            spec.recorder_cm as rec,
            sf.SoundFile(
                str(spec.out_path),
                mode="w",
                samplerate=spec.sample_rate,
                channels=spec.channels,
                subtype=spec.subtype,
                format=spec.sf_format,
            ) as f,
        ):
            while not stop.is_set():
                data = rec.record(numframes=chunk_frames)
                f.write(data)
                # Peak amplitude over the chunk. soundcard returns float32 in
                # [-1, 1] so this stays bounded. Cheap (~100 µs for a 1600-
                # frame stereo block) and runs once per CHUNK_SECONDS.
                try:
                    peak = float(np.abs(data).max()) if data.size else 0.0
                except Exception:
                    peak = 0.0
                spec.on_level(peak)
        log.info("[%s] stopped cleanly", spec.label)
    except Exception:
        log.exception("[%s] recording failed", spec.label)
        raise


class DualRecorder:
    """Records mic + system loopback into two separate audio files.

    Usage:
        rec = DualRecorder(out_dir, sample_rate=16000, format="opus")
        rec.start()
        ...  # capture runs in background threads
        rec.stop()
    """

    def __init__(
        self,
        out_dir: Path,
        mic_name: str | None = None,
        speaker_name: str | None = None,
        *,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        format: str = DEFAULT_FORMAT,
    ) -> None:
        fmt = format.lower()
        if fmt not in _FORMAT_SPECS:
            raise ValueError(f"Unsupported format {format!r}. Use one of: {SUPPORTED_FORMATS}")
        sf_format, subtype, ext = _FORMAT_SPECS[fmt]
        self.sample_rate = sample_rate
        self.format = fmt

        self.out_dir = out_dir
        self.mic_path = out_dir / f"mic.{ext}"
        self.system_path = out_dir / f"system.{ext}"

        # Live peak levels, updated by the capture threads on every chunk.
        # Plain float writes are atomic under the GIL — no lock needed for a
        # ~10 Hz reader (the GUI tick).
        self.mic_level: float = 0.0
        self.system_level: float = 0.0

        chunk_frames = int(sample_rate * CHUNK_SECONDS)
        driver_blocksize = _driver_blocksize(chunk_frames)
        mic = devices.get_mic(mic_name)
        loopback = devices.get_system_loopback(speaker_name)

        self._specs = [
            TrackSpec(
                label="mic",
                recorder_cm=mic.recorder(
                    samplerate=sample_rate, channels=1, blocksize=driver_blocksize
                ),
                out_path=self.mic_path,
                channels=1,
                sample_rate=sample_rate,
                sf_format=sf_format,
                subtype=subtype,
                on_level=self._set_mic_level,
            ),
            TrackSpec(
                label="system",
                recorder_cm=loopback.recorder(
                    samplerate=sample_rate, channels=2, blocksize=driver_blocksize
                ),
                out_path=self.system_path,
                channels=2,
                sample_rate=sample_rate,
                sf_format=sf_format,
                subtype=subtype,
                on_level=self._set_system_level,
            ),
        ]
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def _set_mic_level(self, value: float) -> None:
        self.mic_level = value

    def _set_system_level(self, value: float) -> None:
        self.system_level = value

    def start(self) -> None:
        if self._threads:
            raise RuntimeError("Already started")
        self._stop.clear()
        for spec in self._specs:
            t = threading.Thread(target=_stream_track, args=(spec, self._stop), daemon=False)
            t.start()
            self._threads.append(t)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=timeout)
        self._threads = []
