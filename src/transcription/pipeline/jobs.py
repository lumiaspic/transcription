"""SQLite-backed persistent job queue.

Any process can enqueue jobs safely (WAL journal mode). Only one worker
should consume at a time; multiple consumers would not corrupt anything
thanks to the atomic UPDATE...RETURNING claim, but they'd contend pointlessly.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ..paths import jobs_db

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recording_id TEXT NOT NULL,
    recording_dir TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    model TEXT,
    language TEXT,
    diarize INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at);
"""

VALID_STATUSES = ("pending", "running", "done", "failed")


@dataclass
class Job:
    id: int
    recording_id: str
    recording_dir: str
    status: str
    model: str | None
    language: str | None
    diarize: bool
    created_at: str
    started_at: str | None
    finished_at: str | None
    error: str | None
    attempts: int

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Job:
        return cls(
            id=row["id"],
            recording_id=row["recording_id"],
            recording_dir=row["recording_dir"],
            status=row["status"],
            model=row["model"],
            language=row["language"],
            diarize=bool(row["diarize"]),
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            error=row["error"],
            attempts=row["attempts"],
        )


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


class JobQueue:
    """Thin SQLite wrapper. One connection per operation to keep things stateless."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or jobs_db()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)
            c.execute("PRAGMA journal_mode=WAL")

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        # isolation_level=None -> autocommit; we use explicit single-statement updates,
        # so transactions are atomic per call.
        conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # ---------- producer side ----------

    def enqueue(
        self,
        recording_id: str,
        recording_dir: Path,
        *,
        model: str | None = None,
        language: str | None = None,
        diarize: bool = True,
    ) -> int:
        with self._conn() as c:
            cur = c.execute(
                """INSERT INTO jobs (recording_id, recording_dir, model, language, diarize, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (recording_id, str(recording_dir), model, language, int(diarize), _now()),
            )
            return int(cur.lastrowid)

    # ---------- consumer side ----------

    def claim_next(self) -> Job | None:
        """Atomically pick the oldest pending job and mark it running.

        Uses SQLite's UPDATE...RETURNING (>= 3.35) so the claim is a single
        statement -- no race window between SELECT and UPDATE.
        """
        with self._conn() as c:
            row = c.execute(
                """UPDATE jobs
                   SET status='running', started_at=?, attempts=attempts+1
                   WHERE id = (
                       SELECT id FROM jobs WHERE status='pending'
                       ORDER BY created_at LIMIT 1
                   )
                   RETURNING *""",
                (_now(),),
            ).fetchone()
            return Job.from_row(row) if row else None

    def mark_done(self, job_id: int) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE jobs SET status='done', finished_at=?, error=NULL WHERE id=?",
                (_now(), job_id),
            )

    def mark_failed(self, job_id: int, error: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE jobs SET status='failed', finished_at=?, error=? WHERE id=?",
                (_now(), error[:4000], job_id),
            )

    def retry(self, job_id: int) -> bool:
        """Reset a failed job to pending. Returns True if it was re-queued."""
        with self._conn() as c:
            cur = c.execute(
                """UPDATE jobs
                   SET status='pending', started_at=NULL, finished_at=NULL, error=NULL
                   WHERE id=? AND status='failed'""",
                (job_id,),
            )
            return cur.rowcount > 0

    def recover_orphans(self) -> int:
        """Reset jobs that were 'running' (daemon died mid-job) back to 'pending'.

        Called by the worker at startup. Without this, a crashed job sits forever.
        """
        with self._conn() as c:
            cur = c.execute(
                "UPDATE jobs SET status='pending', started_at=NULL WHERE status='running'"
            )
            return cur.rowcount

    # ---------- reads ----------

    def list_jobs(self, status: str | None = None, limit: int = 50) -> list[Job]:
        with self._conn() as c:
            if status and status != "all":
                rows = c.execute(
                    "SELECT * FROM jobs WHERE status=? ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [Job.from_row(r) for r in rows]

    def get(self, job_id: int) -> Job | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return Job.from_row(row) if row else None

    def has_active(self) -> bool:
        """Any pending or running job? Used by `record` to warn if no daemon is running."""
        with self._conn() as c:
            row = c.execute(
                "SELECT 1 FROM jobs WHERE status IN ('pending','running') LIMIT 1"
            ).fetchone()
            return row is not None
