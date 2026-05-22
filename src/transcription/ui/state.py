"""Shared mutable state for the UI session.

There is one AppState per GUI process. The recorder lives here so the
Stop button (which fires in a separate handler) can reach the active
recorder started by the Start button. The Worker also lives here so the
UI can show its liveness and clean up cleanly on shutdown.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..audio.recorder import DualRecorder
from ..pipeline.jobs import JobQueue
from ..pipeline.worker import Worker

log = logging.getLogger(__name__)


@dataclass
class RecordingState:
    active: bool = False
    started_at: float = 0.0
    rec_id: str = ""
    rec_dir: Path = field(default_factory=Path)
    recorder: Optional[DualRecorder] = None


class AppState:
    def __init__(self) -> None:
        self.recording = RecordingState()
        self.queue = JobQueue()
        self.worker: Optional[Worker] = None
        self.worker_thread: Optional[threading.Thread] = None

    # ---------- worker ----------

    def start_worker(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            return
        self.worker = Worker()
        self.worker_thread = threading.Thread(
            target=self.worker.run_forever, daemon=True, name="transcription-worker",
        )
        self.worker_thread.start()
        log.info("Worker thread started")

    def worker_alive(self) -> bool:
        return self.worker_thread is not None and self.worker_thread.is_alive()

    # ---------- recording ----------

    def begin_recording(self, rec_id: str, rec_dir: Path) -> None:
        recorder = DualRecorder(rec_dir)
        recorder.start()
        self.recording = RecordingState(
            active=True,
            started_at=time.time(),
            rec_id=rec_id,
            rec_dir=rec_dir,
            recorder=recorder,
        )

    def end_recording(self) -> float:
        """Stop and return elapsed seconds. Safe to call when not recording."""
        if not self.recording.active or self.recording.recorder is None:
            return 0.0
        self.recording.recorder.stop()
        elapsed = time.time() - self.recording.started_at
        self.recording.active = False
        return elapsed

    def recording_elapsed(self) -> float:
        if not self.recording.active:
            return 0.0
        return time.time() - self.recording.started_at


# Singleton -- one AppState per process, created lazily on first import.
STATE = AppState()
