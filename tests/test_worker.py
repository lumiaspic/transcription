"""Tests for `pipeline.worker` — the background queue drainer.

The worker indirectly calls `transcribe.run_transcription`, which calls
`backend.transcribe()`. We monkeypatch the worker's `get_backend` so it
returns our FakeBackend (no torch, no actual transcription).

`run_forever` is tested by replacing `time.sleep` with a function that
raises KeyboardInterrupt — the worker treats Ctrl+C as the clean exit signal.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import FakeBackend
from transcription.pipeline import worker as worker_mod
from transcription.pipeline.jobs import JobQueue
from transcription.pipeline.worker import Worker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_rec_dir(tmp_path: Path, rec_id: str = "rec") -> Path:
    """Create a recording dir with a mic track so run_transcription has work to do."""
    d = tmp_path / rec_id
    d.mkdir(parents=True)
    (d / "mic.flac").write_bytes(b"")
    return d


@pytest.fixture
def queue(tmp_path: Path) -> JobQueue:
    return JobQueue(db_path=tmp_path / "jobs.db")


@pytest.fixture
def stub_get_backend(monkeypatch: pytest.MonkeyPatch) -> FakeBackend:
    """Replace worker.get_backend with a stub returning a singleton FakeBackend.

    Yields the FakeBackend so tests can inspect `.calls`.
    """
    fake = FakeBackend()
    monkeypatch.setattr(worker_mod, "get_backend_chain", lambda model=None: [fake])
    return fake


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestInit:
    def test_uses_provided_queue(self, queue: JobQueue) -> None:
        w = Worker(queue=queue)
        assert w.queue is queue

    def test_starts_with_empty_backend_cache(self, queue: JobQueue) -> None:
        assert Worker(queue=queue)._backends == {}

    def test_default_poll_interval_is_two_seconds(self, queue: JobQueue) -> None:
        assert Worker(queue=queue).poll_interval == 2.0


# ---------------------------------------------------------------------------
# _get_backend
# ---------------------------------------------------------------------------


class TestGetBackendMemoization:
    def test_first_call_constructs_and_caches(
        self, queue: JobQueue, stub_get_backend: FakeBackend
    ) -> None:
        w = Worker(queue=queue)

        chain = w._get_backend_chain("medium")

        assert chain == [stub_get_backend]
        assert "medium" in w._backends

    def test_second_call_with_same_model_returns_cached_instance(
        self,
        queue: JobQueue,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Track how many times get_backend_chain is called.
        calls: list[str | None] = []

        def fake_get_chain(model: str | None = None) -> list[FakeBackend]:
            calls.append(model)
            return [FakeBackend()]

        monkeypatch.setattr(worker_mod, "get_backend_chain", fake_get_chain)
        w = Worker(queue=queue)

        first = w._get_backend_chain("small")
        second = w._get_backend_chain("small")

        assert first is second
        # Critical for performance: avoids re-loading a 13s Whisper model.
        assert len(calls) == 1

    def test_different_model_names_get_distinct_backends(
        self,
        queue: JobQueue,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(worker_mod, "get_backend_chain", lambda model=None: [FakeBackend()])
        w = Worker(queue=queue)

        a = w._get_backend_chain("small")
        b = w._get_backend_chain("large-v3")

        assert a is not b
        assert set(w._backends.keys()) == {"small", "large-v3"}

    def test_none_model_uses_distinct_cache_key(
        self,
        queue: JobQueue,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # None means "use config default" — kept distinct from explicit names
        # so a job pinned to "small" never shares a cached backend with a
        # job that lets the config decide.
        monkeypatch.setattr(worker_mod, "get_backend_chain", lambda model=None: [FakeBackend()])
        w = Worker(queue=queue)

        w._get_backend_chain(None)
        w._get_backend_chain("small")

        assert "__config_default__" in w._backends
        assert "small" in w._backends


# ---------------------------------------------------------------------------
# _execute
# ---------------------------------------------------------------------------


class TestExecuteSuccess:
    def test_marks_job_done_when_transcription_succeeds(
        self,
        tmp_path: Path,
        queue: JobQueue,
        stub_get_backend: FakeBackend,  # noqa: ARG002
    ) -> None:
        rec_dir = _setup_rec_dir(tmp_path, "rec_ok")
        job_id = queue.enqueue(recording_id="rec_ok", recording_dir=rec_dir)
        job = queue.claim_next()  # status → running
        assert job is not None

        Worker(queue=queue)._execute(job)

        post = queue.get(job_id)
        assert post is not None
        assert post.status == "done"
        assert post.error is None

    def test_writes_per_track_outputs_via_run_transcription(
        self,
        tmp_path: Path,
        queue: JobQueue,
        stub_get_backend: FakeBackend,  # noqa: ARG002
    ) -> None:
        rec_dir = _setup_rec_dir(tmp_path, "rec_out")
        queue.enqueue(recording_id="rec_out", recording_dir=rec_dir)
        job = queue.claim_next()
        assert job is not None

        Worker(queue=queue)._execute(job)

        # Sanity: integration with the format/merge modules really happened.
        assert (rec_dir / "mic.txt").exists()
        assert (rec_dir / "transcript.md").exists()
        meta = json.loads((rec_dir / "meta.json").read_text(encoding="utf-8"))
        assert meta["transcribed"] is True


class TestExecuteFailure:
    def test_marks_job_failed_with_repr_of_exception(
        self,
        tmp_path: Path,
        queue: JobQueue,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # A backend that always raises — simulates e.g. CUDA OOM.
        class BrokenBackend(FakeBackend):
            def transcribe(self, *args, **kwargs):  # type: ignore[override]
                raise RuntimeError("CUDA out of memory")

        monkeypatch.setattr(worker_mod, "get_backend_chain", lambda model=None: [BrokenBackend()])

        rec_dir = _setup_rec_dir(tmp_path, "rec_fail")
        job_id = queue.enqueue(recording_id="rec_fail", recording_dir=rec_dir)
        job = queue.claim_next()
        assert job is not None

        # _execute must catch and log the exception, NOT re-raise — otherwise a
        # single bad job kills the entire daemon.
        Worker(queue=queue)._execute(job)

        post = queue.get(job_id)
        assert post is not None
        assert post.status == "failed"
        assert "CUDA out of memory" in (post.error or "")


# ---------------------------------------------------------------------------
# run_forever
# ---------------------------------------------------------------------------


class TestRunForever:
    def test_drains_a_pending_job_then_stops_on_keyboardinterrupt(
        self,
        tmp_path: Path,
        queue: JobQueue,
        stub_get_backend: FakeBackend,  # noqa: ARG002
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Tactic: enqueue 1 job. The worker claims+executes it, then loops
        # back, sees no pending job, and calls time.sleep — at which point
        # we raise KeyboardInterrupt to exit cleanly.
        _setup_rec_dir(tmp_path, "the_only_job")
        job_id = queue.enqueue(recording_id="the_only_job", recording_dir=tmp_path / "the_only_job")

        def _interrupt(_: float) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(worker_mod.time, "sleep", _interrupt)

        Worker(queue=queue, poll_interval=0.01).run_forever()

        post = queue.get(job_id)
        assert post is not None
        assert post.status == "done"

    def test_recovers_orphans_at_startup_before_polling(
        self,
        tmp_path: Path,
        queue: JobQueue,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Simulate a crashed daemon: a job left in 'running' state.
        queue.enqueue(recording_id="orphan", recording_dir=tmp_path / "orphan")
        queue.claim_next()
        assert queue.list_jobs(status="running")[0].recording_id == "orphan"

        # Exit on the first sleep so we don't try to re-execute the orphan
        # (it has no audio files, would fail). We only care that the orphan
        # was reset to pending BEFORE the loop kicked in.
        seen_pending_during_run: list[bool] = []

        def _interrupt(_: float) -> None:
            seen_pending_during_run.append(bool(queue.list_jobs(status="pending")))
            raise KeyboardInterrupt

        # No FakeBackend stub — but the orphan recover should happen BEFORE
        # any backend lookup, so we never reach that code path.
        monkeypatch.setattr(worker_mod.time, "sleep", _interrupt)

        # Pre-claim everything so claim_next returns None immediately and
        # the loop hits sleep right after the recovery.
        # Hmm, actually after recover_orphans, the job is now pending again.
        # claim_next will pick it up. We need the loop to never actually run
        # this job. Easiest: make claim_next return None.
        monkeypatch.setattr(queue, "claim_next", lambda: None)

        Worker(queue=queue, poll_interval=0.01).run_forever()

        # We hit the sleep at least once, and at that point the orphan was
        # already moved from 'running' back to 'pending'.
        assert seen_pending_during_run, "run_forever didn't reach the sleep"
        assert seen_pending_during_run[0] is True
