"""Background worker that drains the SQLite job queue.

The worker process is started via `transcription daemon` and stays alive
until Ctrl+C. It claims pending jobs one at a time, executes them, and
updates their status.

Model cache: loading a Whisper model takes ~13s. The worker keeps one
backend instance per model name in memory so jobs that share a model
reuse the loaded weights -- a 100x speedup for the model-load phase.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from ..backends.base import TranscriptionBackend
from ..backends.factory import get_backend
from .jobs import Job, JobQueue
from .transcribe import run_transcription

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, queue: JobQueue | None = None, poll_interval: float = 2.0) -> None:
        self.queue = queue or JobQueue()
        self.poll_interval = poll_interval
        # Keyed by model name (None means "use config default", kept distinct as well).
        self._backends: dict[str, TranscriptionBackend] = {}

    # ---------- public API ----------

    def run_forever(self) -> None:
        recovered = self.queue.recover_orphans()
        if recovered:
            log.warning("Recovered %d orphan job(s) (running -> pending)", recovered)
        log.info("Worker ready. Polling every %.1fs. Ctrl+C to stop.", self.poll_interval)
        try:
            while True:
                job = self.queue.claim_next()
                if job is None:
                    time.sleep(self.poll_interval)
                    continue
                self._execute(job)
        except KeyboardInterrupt:
            log.info("Worker stopped by user (Ctrl+C)")

    # ---------- internals ----------

    def _get_backend(self, model: str | None) -> TranscriptionBackend:
        key = model or "__config_default__"
        if key not in self._backends:
            log.info("Loading backend (model=%s)", model or "config default")
            self._backends[key] = get_backend(model=model)
        return self._backends[key]

    def _execute(self, job: Job) -> None:
        log.info(
            "Job %d running: rec=%s model=%s diarize=%s",
            job.id, job.recording_id, job.model or "config-default", job.diarize,
        )
        try:
            backend = self._get_backend(job.model)
            run_transcription(
                rec_dir=Path(job.recording_dir),
                backend=backend,
                language=job.language,
                diarize=job.diarize,
                progress=lambda msg: log.info("  %s", msg),
            )
            self.queue.mark_done(job.id)
            log.info("Job %d done", job.id)
        except Exception as e:
            log.exception("Job %d failed", job.id)
            self.queue.mark_failed(job.id, repr(e))
