"""Tests for `pipeline.recovery` — orphan recording detection + finalization.

We generate real (tiny) WAV files via `soundfile`, so the duration probe
exercises the actual code path rather than a mock — soundfile is in the
base deps and works fine cross-platform.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from transcription.pipeline import recovery
from transcription.pipeline.jobs import JobQueue
from transcription.pipeline.recovery import (
    Orphan,
    find_orphans,
    recover_all,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_silence(path: Path, duration_s: float, sample_rate: int = 16_000) -> None:
    """Write a real, valid audio file of `duration_s` seconds of silence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(duration_s * sample_rate)
    audio = np.zeros(n, dtype=np.float32)
    sf.write(str(path), audio, sample_rate, subtype="PCM_16")


def _make_orphan_dir(root: Path, rec_id: str, *, mic_dur: float = 1.0) -> Path:
    """Create a recording dir with a mic track but NO meta.json (= orphan)."""
    d = root / rec_id
    _write_silence(d / "mic.wav", mic_dur)
    return d


def _make_clean_dir(root: Path, rec_id: str) -> Path:
    """A clean recording: audio + meta.json (= NOT an orphan)."""
    d = _make_orphan_dir(root, rec_id)
    (d / "meta.json").write_text(json.dumps({"id": rec_id}), encoding="utf-8")
    return d


# ---------------------------------------------------------------------------
# find_orphans
# ---------------------------------------------------------------------------


class TestFindOrphans:
    def test_returns_empty_list_when_root_missing(self, tmp_path: Path) -> None:
        # E.g. fresh install: recordings/ has never been created.
        assert find_orphans(tmp_path / "nope") == []

    def test_returns_empty_list_when_root_empty(self, tmp_path: Path) -> None:
        assert find_orphans(tmp_path) == []

    def test_detects_a_dir_with_audio_but_no_meta(self, tmp_path: Path) -> None:
        _make_orphan_dir(tmp_path, "orphan_1")

        orphans = find_orphans(tmp_path)

        assert len(orphans) == 1
        assert orphans[0].rec_id == "orphan_1"
        assert orphans[0].tracks_found == ["mic"]
        assert orphans[0].duration_seconds == pytest.approx(1.0, abs=0.05)

    def test_skips_dirs_that_already_have_meta_json(self, tmp_path: Path) -> None:
        # User explicitly chose --no-transcribe → meta exists, transcribed=False.
        # We must NOT second-guess that and re-enqueue.
        _make_clean_dir(tmp_path, "clean_recording")

        assert find_orphans(tmp_path) == []

    def test_skips_dirs_with_no_audio_tracks(self, tmp_path: Path) -> None:
        # Empty dir or one with only meta.json from a failed creation.
        (tmp_path / "empty").mkdir()

        assert find_orphans(tmp_path) == []

    def test_ignores_non_directory_entries(self, tmp_path: Path) -> None:
        # A stray file at the root of recordings/ shouldn't crash the scan.
        (tmp_path / "stray.txt").write_text("not a recording")

        assert find_orphans(tmp_path) == []

    def test_uses_longest_track_as_canonical_duration(self, tmp_path: Path) -> None:
        # Real-world: mid-crash, mic.wav might be shorter than system.wav.
        # The reported duration must be the *max* so the user sees what was
        # actually captured.
        d = tmp_path / "uneven"
        _write_silence(d / "mic.wav", duration_s=0.5)
        _write_silence(d / "system.wav", duration_s=2.0)

        orphans = find_orphans(tmp_path)

        assert len(orphans) == 1
        assert orphans[0].duration_seconds == pytest.approx(2.0, abs=0.05)
        assert set(orphans[0].tracks_found) == {"mic", "system"}


# ---------------------------------------------------------------------------
# finalize_orphan
# ---------------------------------------------------------------------------


class TestFinalizeOrphan:
    def test_writes_meta_json_with_expected_keys(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Avoid hitting the real JobQueue / paths: call with enqueue=False.
        d = _make_orphan_dir(tmp_path, "rec_meta", mic_dur=1.5)
        orphan = find_orphans(tmp_path)[0]

        job_id = recovery.finalize_orphan(orphan, enqueue=False)

        assert job_id is None
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        # Required fields the rest of the app reads back.
        for key in (
            "id",
            "created_at",
            "duration_seconds",
            "tracks",
            "sample_rate",
            "format",
            "transcribed",
            "recovered",
        ):
            assert key in meta, f"meta.json missing {key}"
        assert meta["id"] == "rec_meta"
        assert meta["transcribed"] is False  # never auto-transcribed
        assert meta["recovered"] is True  # marker so the user knows
        assert meta["sample_rate"] == 16_000
        assert meta["format"] == "wav"
        assert meta["duration_seconds"] == pytest.approx(1.5, abs=0.1)

    @pytest.mark.skipif(not sf.check_format("OGG", "OPUS"), reason="libsndfile built without Opus")
    def test_records_opus_format_not_ogg_container(self, tmp_path: Path) -> None:
        # A recovered Opus orphan must report "opus" (what `record` writes),
        # not libsndfile's "ogg" container name.
        d = tmp_path / "rec_opus"
        d.mkdir()
        n = 16_000
        sf.write(str(d / "mic.opus"), np.zeros(n, dtype=np.float32), 16_000, format="OGG")
        orphan = find_orphans(tmp_path)[0]

        recovery.finalize_orphan(orphan, enqueue=False)

        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        assert meta["format"] == "opus"

    def test_enqueues_job_in_provided_queue_by_default(self, tmp_path: Path) -> None:
        _make_orphan_dir(tmp_path, "rec_enqueue")
        orphan = find_orphans(tmp_path)[0]
        queue = JobQueue(db_path=tmp_path / "jobs.db")

        job_id = recovery.finalize_orphan(orphan, queue=queue)

        assert job_id is not None
        job = queue.get(job_id)
        assert job is not None
        assert job.recording_id == "rec_enqueue"
        assert job.status == "pending"


# ---------------------------------------------------------------------------
# recover_all
# ---------------------------------------------------------------------------


class TestRecoverAll:
    def test_finalizes_every_orphan_and_skips_clean_dirs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Two orphans + one clean recording.
        _make_orphan_dir(tmp_path, "orphan_a")
        _make_orphan_dir(tmp_path, "orphan_b")
        _make_clean_dir(tmp_path, "clean")

        # `recover_all` calls `find_orphans()` with no args → uses the default
        # `recordings_dir()`. Monkeypatch that to our tmp_path.
        monkeypatch.setattr(recovery, "recordings_dir", lambda: tmp_path)
        queue = JobQueue(db_path=tmp_path / "jobs.db")

        out = recover_all(queue=queue)

        assert len(out) == 2
        rec_ids = {orphan.rec_id for orphan, _ in out}
        assert rec_ids == {"orphan_a", "orphan_b"}
        # Both jobs were enqueued.
        for _, job_id in out:
            assert job_id is not None
        assert len(queue.list_jobs(status="pending")) == 2

    def test_enqueue_false_still_writes_meta_but_skips_queue(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_orphan_dir(tmp_path, "rec_noenq")
        monkeypatch.setattr(recovery, "recordings_dir", lambda: tmp_path)

        out = recover_all(enqueue=False)

        assert len(out) == 1
        orphan, job_id = out[0]
        assert job_id is None
        assert (orphan.rec_dir / "meta.json").exists()


# ---------------------------------------------------------------------------
# Dataclass smoke test (cheap but pins the public shape)
# ---------------------------------------------------------------------------


def test_orphan_dataclass_field_names() -> None:
    o = Orphan(rec_id="x", rec_dir=Path("/tmp/x"), tracks_found=["mic"], duration_seconds=1.0)
    assert o.rec_id == "x"
    assert o.tracks_found == ["mic"]
