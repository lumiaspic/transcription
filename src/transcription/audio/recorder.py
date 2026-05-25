"""Dual-track recorder: mic + system loopback, written as 2 separate audio files.

Streams to disk in small chunks - bounded memory regardless of recording length.

Format and sample rate are configurable (see config.recording_format and
config.recording_sample_rate). Defaults are FLAC at 16 kHz: WhisperX and
pyannote both resample to 16 kHz internally, so anything higher is bytes
on disk that the pipeline immediately discards.

Backward-compat: old recordings on disk as .wav remain readable thanks to
the format-agnostic track lookup in pipeline/transcribe.py.
"""

from __future__ import annotations

import logging
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import soundfile as sf

from . import devices

log = logging.getLogger(__name__)

# Defaults used when no override is passed and no config exists. Constructor
# arguments and the config keys take precedence over these.
DEFAULT_SAMPLE_RATE = 16_000
DEFAULT_FORMAT = "flac"
SUPPORTED_FORMATS = ("flac", "wav")

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
    recorder_cm: object  # soundcard recorder context manager
    out_path: Path
    channels: int
    sample_rate: int
    sf_format: str  # "FLAC" or "WAV" -- passed to sf.SoundFile


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
                subtype="PCM_16",
                format=spec.sf_format,
            ) as f,
        ):
            while not stop.is_set():
                data = rec.record(numframes=chunk_frames)
                f.write(data)
        log.info("[%s] stopped cleanly", spec.label)
    except Exception:
        log.exception("[%s] recording failed", spec.label)
        raise


class DualRecorder:
    """Records mic + system loopback into two separate audio files.

    Usage:
        rec = DualRecorder(out_dir, sample_rate=16000, format="flac")
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
        if fmt not in SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported format {format!r}. Use one of: {SUPPORTED_FORMATS}")
        self.sample_rate = sample_rate
        self.format = fmt

        self.out_dir = out_dir
        self.mic_path = out_dir / f"mic.{fmt}"
        self.system_path = out_dir / f"system.{fmt}"

        chunk_frames = int(sample_rate * CHUNK_SECONDS)
        driver_blocksize = _driver_blocksize(chunk_frames)
        mic = devices.get_mic(mic_name)
        loopback = devices.get_system_loopback(speaker_name)
        sf_format = fmt.upper()

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
            ),
        ]
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

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
