"""Tests for `pipeline.jobs` — the SQLite-backed job queue.

Each test gets its own DB file in a tmp dir (the JobQueue constructor
accepts a `db_path` arg, so we don't need to mock `paths.jobs_db()`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from transcription.pipeline.jobs import VALID_STATUSES, Job, JobQueue


@pytest.fixture
def queue(tmp_path: Path) -> JobQueue:
    """Fresh queue per test, backed by a real SQLite file in tmp_path."""
    return JobQueue(db_path=tmp_path / "jobs.db")


class TestEnqueue:
    def test_returns_an_integer_id_starting_at_one(self, queue: JobQueue, tmp_path: Path) -> None:
        job_id = queue.enqueue(recording_id="rec_1", recording_dir=tmp_path / "rec_1")

        # AUTOINCREMENT starts at 1, not 0 — this is also what callers display.
        assert job_id == 1
        assert isinstance(job_id, int)

    def test_consecutive_enqueues_get_distinct_increasing_ids(
        self, queue: JobQueue, tmp_path: Path
    ) -> None:
        a = queue.enqueue(recording_id="a", recording_dir=tmp_path / "a")
        b = queue.enqueue(recording_id="b", recording_dir=tmp_path / "b")
        c = queue.enqueue(recording_id="c", recording_dir=tmp_path / "c")

        assert a < b < c

    def test_persists_all_user_supplied_fields(self, queue: JobQueue, tmp_path: Path) -> None:
        job_id = queue.enqueue(
            recording_id="rec_42",
            recording_dir=tmp_path / "rec_42",
            model="large-v3",
            language="fr",
            diarize=False,
        )

        job = queue.get(job_id)
        assert job is not None
        assert job.recording_id == "rec_42"
        assert job.recording_dir == str(tmp_path / "rec_42")
        assert job.model == "large-v3"
        assert job.language == "fr"
        assert job.diarize is False
        assert job.status == "pending"
        assert job.attempts == 0
        assert job.created_at  # any non-empty ISO timestamp


class TestClaimNext:
    def test_returns_none_on_empty_queue(self, queue: JobQueue) -> None:
        assert queue.claim_next() is None

    def test_returns_oldest_pending_job_and_marks_it_running(
        self, queue: JobQueue, tmp_path: Path
    ) -> None:
        first = queue.enqueue(recording_id="first", recording_dir=tmp_path / "first")
        queue.enqueue(recording_id="second", recording_dir=tmp_path / "second")

        claimed = queue.claim_next()

        assert claimed is not None
        assert claimed.id == first
        assert claimed.status == "running"
        assert claimed.started_at is not None
        assert claimed.attempts == 1  # incremented atomically with the claim

    def test_skips_jobs_already_running(self, queue: JobQueue, tmp_path: Path) -> None:
        queue.enqueue(recording_id="a", recording_dir=tmp_path / "a")
        queue.enqueue(recording_id="b", recording_dir=tmp_path / "b")

        first = queue.claim_next()
        second = queue.claim_next()

        assert first is not None and second is not None
        assert first.id != second.id  # each claim gets a different job

    def test_returns_none_when_only_running_or_done_jobs_remain(
        self, queue: JobQueue, tmp_path: Path
    ) -> None:
        queue.enqueue(recording_id="only", recording_dir=tmp_path / "only")
        queue.claim_next()  # now running, nothing pending

        assert queue.claim_next() is None


class TestMarkDoneAndFailed:
    def test_mark_done_sets_status_and_clears_error(self, queue: JobQueue, tmp_path: Path) -> None:
        job_id = queue.enqueue(recording_id="x", recording_dir=tmp_path / "x")
        queue.mark_failed(job_id, "first try failed")

        queue.mark_done(job_id)

        job = queue.get(job_id)
        assert job is not None
        assert job.status == "done"
        assert job.error is None
        assert job.finished_at is not None

    def test_mark_failed_stores_truncated_error(self, queue: JobQueue, tmp_path: Path) -> None:
        # The error column has a 4000-char cap to prevent giant tracebacks
        # from bloating the DB. Test the truncation explicitly.
        huge_error = "x" * 10_000
        job_id = queue.enqueue(recording_id="x", recording_dir=tmp_path / "x")

        queue.mark_failed(job_id, huge_error)

        job = queue.get(job_id)
        assert job is not None
        assert job.status == "failed"
        assert job.error is not None
        assert len(job.error) == 4000


class TestRetry:
    def test_failed_job_can_be_retried_back_to_pending(
        self, queue: JobQueue, tmp_path: Path
    ) -> None:
        job_id = queue.enqueue(recording_id="x", recording_dir=tmp_path / "x")
        queue.mark_failed(job_id, "boom")

        re_queued = queue.retry(job_id)

        assert re_queued is True
        job = queue.get(job_id)
        assert job is not None
        assert job.status == "pending"
        assert job.error is None
        assert job.started_at is None
        assert job.finished_at is None

    def test_cannot_retry_a_done_job(self, queue: JobQueue, tmp_path: Path) -> None:
        # Retrying a successful job would silently re-run it — refuse instead.
        job_id = queue.enqueue(recording_id="x", recording_dir=tmp_path / "x")
        queue.claim_next()
        queue.mark_done(job_id)

        assert queue.retry(job_id) is False

    def test_retry_of_unknown_id_returns_false(self, queue: JobQueue) -> None:
        assert queue.retry(999_999) is False


class TestRecoverOrphans:
    def test_resets_running_jobs_back_to_pending(self, queue: JobQueue, tmp_path: Path) -> None:
        # Simulate a daemon crash: a job is left in 'running' with no worker.
        queue.enqueue(recording_id="x", recording_dir=tmp_path / "x")
        queue.claim_next()  # status is now 'running'

        recovered = queue.recover_orphans()

        assert recovered == 1
        all_pending = queue.list_jobs(status="pending")
        assert len(all_pending) == 1
        assert all_pending[0].started_at is None  # cleared too

    def test_returns_zero_when_no_running_jobs(self, queue: JobQueue) -> None:
        assert queue.recover_orphans() == 0


class TestListJobs:
    def test_default_lists_all_jobs_newest_first(
        self, queue: JobQueue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # `_now()` has 1-second resolution; 3 quick enqueues in the same test
        # would collide and the ORDER BY DESC tie-breaker becomes implementation-
        # defined. Inject distinct, strictly-increasing timestamps so we test the
        # documented behavior (newest-first) deterministically.
        timestamps = iter(["2026-01-01T00:00:00", "2026-01-01T00:00:01", "2026-01-01T00:00:02"])
        monkeypatch.setattr("transcription.pipeline.jobs._now", lambda: next(timestamps))

        queue.enqueue(recording_id="oldest", recording_dir=tmp_path / "a")
        queue.enqueue(recording_id="middle", recording_dir=tmp_path / "b")
        queue.enqueue(recording_id="newest", recording_dir=tmp_path / "c")

        jobs = queue.list_jobs()

        assert [j.recording_id for j in jobs] == ["newest", "middle", "oldest"]

    @pytest.mark.parametrize("status_filter", VALID_STATUSES)
    def test_filters_by_status_when_provided(
        self, queue: JobQueue, tmp_path: Path, status_filter: str
    ) -> None:
        # Each status touched at least once.
        a = queue.enqueue(recording_id="a", recording_dir=tmp_path / "a")
        b = queue.enqueue(recording_id="b", recording_dir=tmp_path / "b")
        queue.claim_next()  # a → running
        queue.mark_done(a)  # a → done
        queue.mark_failed(b, "err")  # b → failed
        queue.enqueue(recording_id="c", recording_dir=tmp_path / "c")  # c → pending

        filtered = queue.list_jobs(status=status_filter)

        for job in filtered:
            assert job.status == status_filter

    def test_status_all_acts_like_no_filter(self, queue: JobQueue, tmp_path: Path) -> None:
        queue.enqueue(recording_id="x", recording_dir=tmp_path / "x")
        queue.enqueue(recording_id="y", recording_dir=tmp_path / "y")

        assert len(queue.list_jobs(status="all")) == 2

    def test_respects_limit(self, queue: JobQueue, tmp_path: Path) -> None:
        for i in range(5):
            queue.enqueue(recording_id=f"r{i}", recording_dir=tmp_path / f"r{i}")

        assert len(queue.list_jobs(limit=2)) == 2


class TestGet:
    def test_returns_job_for_known_id(self, queue: JobQueue, tmp_path: Path) -> None:
        job_id = queue.enqueue(recording_id="x", recording_dir=tmp_path / "x")

        job = queue.get(job_id)

        assert isinstance(job, Job)
        assert job.id == job_id

    def test_returns_none_for_unknown_id(self, queue: JobQueue) -> None:
        assert queue.get(999_999) is None


class TestHasActive:
    def test_false_on_empty_queue(self, queue: JobQueue) -> None:
        assert queue.has_active() is False

    def test_true_when_a_job_is_pending(self, queue: JobQueue, tmp_path: Path) -> None:
        queue.enqueue(recording_id="x", recording_dir=tmp_path / "x")
        assert queue.has_active() is True

    def test_true_when_a_job_is_running(self, queue: JobQueue, tmp_path: Path) -> None:
        queue.enqueue(recording_id="x", recording_dir=tmp_path / "x")
        queue.claim_next()
        assert queue.has_active() is True

    def test_false_when_all_jobs_are_done_or_failed(self, queue: JobQueue, tmp_path: Path) -> None:
        a = queue.enqueue(recording_id="a", recording_dir=tmp_path / "a")
        b = queue.enqueue(recording_id="b", recording_dir=tmp_path / "b")
        queue.claim_next()
        queue.claim_next()
        queue.mark_done(a)
        queue.mark_failed(b, "err")

        assert queue.has_active() is False


class TestPersistence:
    def test_jobs_survive_reopening_the_queue(self, tmp_path: Path) -> None:
        # Real-world simulation: enqueue from process A, read from process B
        # (here just two JobQueue instances over the same file).
        db = tmp_path / "shared.db"

        q1 = JobQueue(db_path=db)
        job_id = q1.enqueue(recording_id="persistent", recording_dir=tmp_path / "x")

        q2 = JobQueue(db_path=db)
        job = q2.get(job_id)

        assert job is not None
        assert job.recording_id == "persistent"
