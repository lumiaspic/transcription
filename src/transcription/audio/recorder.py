"""Dual-track recorder: mic + system loopback, written as 2 separate FLAC files.

Streams to disk in small chunks - bounded memory regardless of recording length.

Format: FLAC (lossless, ~50% smaller than the equivalent PCM WAV). WhisperX
loads any audio format via ffmpeg, so downstream code doesn't care. Old
recordings still on disk as .wav remain readable thanks to the backward-
compatible track lookup in pipeline/transcribe.py.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import soundfile as sf

from . import devices

log = logging.getLogger(__name__)

SAMPLE_RATE = 48_000
CHUNK_SECONDS = 0.1  # 100 ms blocks; small enough for responsive stop, big enough to avoid syscall thrash
CHUNK_FRAMES = int(SAMPLE_RATE * CHUNK_SECONDS)
TRACK_EXT = "flac"


@dataclass
class TrackSpec:
    label: str           # "mic" or "system"
    recorder_cm: object  # soundcard recorder context manager
    out_path: Path
    channels: int


def _stream_track(spec: TrackSpec, stop: threading.Event) -> None:
    spec.out_path.parent.mkdir(parents=True, exist_ok=True)
    log.info("[%s] starting -> %s", spec.label, spec.out_path)
    try:
        with spec.recorder_cm as rec, sf.SoundFile(
            str(spec.out_path),
            mode="w",
            samplerate=SAMPLE_RATE,
            channels=spec.channels,
            subtype="PCM_16",
            format="FLAC",
        ) as f:
            while not stop.is_set():
                data = rec.record(numframes=CHUNK_FRAMES)
                f.write(data)
        log.info("[%s] stopped cleanly", spec.label)
    except Exception:
        log.exception("[%s] recording failed", spec.label)
        raise


class DualRecorder:
    """Records mic + system loopback to two separate WAV files.

    Usage:
        rec = DualRecorder(out_dir)
        rec.start()
        ...  # do stuff; capture runs in background
        rec.stop()
    """

    def __init__(
        self,
        out_dir: Path,
        mic_name: str | None = None,
        speaker_name: str | None = None,
    ) -> None:
        self.out_dir = out_dir
        self.mic_path = out_dir / f"mic.{TRACK_EXT}"
        self.system_path = out_dir / f"system.{TRACK_EXT}"

        mic = devices.get_mic(mic_name)
        loopback = devices.get_system_loopback(speaker_name)

        self._specs = [
            TrackSpec(
                label="mic",
                recorder_cm=mic.recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=CHUNK_FRAMES),
                out_path=self.mic_path,
                channels=1,
            ),
            TrackSpec(
                label="system",
                recorder_cm=loopback.recorder(samplerate=SAMPLE_RATE, channels=2, blocksize=CHUNK_FRAMES),
                out_path=self.system_path,
                channels=2,
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
