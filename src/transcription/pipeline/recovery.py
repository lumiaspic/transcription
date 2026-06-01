"""Detect and recover orphan recordings (e.g., PC shut down mid-record).

When the recorder process is killed before `Stop` is pressed, the audio
files have been streamed to disk but `meta.json` was never written --
which means the recording is invisible to `transcription list` and the
job queue, even though the audio is sitting right there.

This module's job is to find those orphans, write a sensible meta.json
(duration computed from the audio file directly), and enqueue a
transcription job. Called at:
  - GUI startup (auto, see `ui/state.py::AppState.recover_orphans`)
  - CLI `transcription recover` (manual)

"Orphan" definition (deliberately narrow):
    A recordings/<id>/ directory that
      - contains at least one mic.* or system.* audio file, AND
      - has NO meta.json.

A recording with `meta.json` (even with transcribed=False) is NOT an
orphan -- the user explicitly chose to keep it un-transcribed (e.g.
`record --no-transcribe`). We don't second-guess that.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import soundfile as sf

from ..paths import recordings_dir
from .jobs import JobQueue
from .transcribe import KNOWN_TRACKS, find_track_file

log = logging.getLogger(__name__)


def _audio_duration(path: Path) -> float:
    """Best-effort duration in seconds. Resistant to truncated/crashed files."""
    try:
        info = sf.info(str(path))
        if info.frames > 0 and info.samplerate > 0:
            return info.frames / info.samplerate
    except Exception as e:
        log.warning("sf.info(%s) failed: %s; falling back to full read", path, e)
    # Some crashed-file headers report 0 frames; reading the whole file gives
    # the real count. Slow on long files but recovery is a one-off path.
    try:
        data, sr = sf.read(str(path))
        return len(data) / sr if sr > 0 else 0.0
    except Exception as e:
        log.warning("sf.read(%s) failed: %s; reporting duration=0", path, e)
        return 0.0


def _audio_metadata(path: Path) -> tuple[int | None, str | None]:
    """Read (sample_rate, format) from an audio file.

    `format` is the file extension — the user-facing name `record` writes
    (e.g. "opus") — not libsndfile's container name, which would report an
    Opus recording as its "ogg" container and drift from a clean stop. Orphan
    track files always carry one of the known extensions, so the extension is
    authoritative. The sample rate comes from libsndfile, or None if the file
    is unreadable (truncated/corrupt mid-crash)."""
    fmt = path.suffix.lstrip(".").lower() or None
    try:
        info = sf.info(str(path))
        sr = int(info.samplerate) if info.samplerate else None
    except Exception:
        sr = None
    return sr, fmt


@dataclass
class Orphan:
    rec_id: str
    rec_dir: Path
    tracks_found: list[str]  # e.g. ["mic", "system"]
    duration_seconds: float  # max across tracks


def find_orphans(root: Path | None = None) -> list[Orphan]:
    """Return all recording dirs that have audio files but no meta.json."""
    root = root or recordings_dir()
    out: list[Orphan] = []
    if not root.exists():
        return out

    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        if (d / "meta.json").exists():
            continue
        tracks_present = [t for t in KNOWN_TRACKS if find_track_file(d, t) is not None]
        if not tracks_present:
            continue
        # Use the longest track as the canonical duration (mic and system
        # should match within a few ms, but a crashed mid-write track
        # might be shorter than its sibling).
        max_dur = 0.0
        for t in tracks_present:
            p = find_track_file(d, t)
            if p is not None:
                dur = _audio_duration(p)
                if dur > max_dur:
                    max_dur = dur
        out.append(
            Orphan(
                rec_id=d.name,
                rec_dir=d,
                tracks_found=tracks_present,
                duration_seconds=max_dur,
            )
        )
    return out


def finalize_orphan(
    orphan: Orphan,
    *,
    queue: JobQueue | None = None,
    enqueue: bool = True,
) -> int | None:
    """Write meta.json + (optionally) enqueue a job. Returns the job id or None."""
    # Read sample_rate + format from one of the recovered tracks so the meta
    # mirrors what `record` would have written on a clean stop.
    sr_meta: int | None = None
    fmt_meta: str | None = None
    for t in orphan.tracks_found:
        p = find_track_file(orphan.rec_dir, t)
        if p is None:
            continue
        sr_meta, fmt_meta = _audio_metadata(p)
        if sr_meta or fmt_meta:
            break

    meta = {
        "id": orphan.rec_id,
        "created_at": dt.datetime.fromtimestamp(orphan.rec_dir.stat().st_mtime).isoformat(
            timespec="seconds"
        ),
        "duration_seconds": round(orphan.duration_seconds, 1),
        "tracks": orphan.tracks_found,
        "sample_rate": sr_meta,
        "format": fmt_meta,
        "transcribed": False,
        # Visible marker so the user knows this didn't come from a clean Stop.
        "recovered": True,
    }
    (orphan.rec_dir / "meta.json").write_text(
        json.dumps(meta, indent=2),
        encoding="utf-8",
    )
    log.info(
        "Recovered orphan %s (duration=%.1fs, tracks=%s)",
        orphan.rec_id,
        orphan.duration_seconds,
        orphan.tracks_found,
    )
    if not enqueue:
        return None
    q = queue or JobQueue()
    return q.enqueue(recording_id=orphan.rec_id, recording_dir=orphan.rec_dir)


def recover_all(
    *,
    queue: JobQueue | None = None,
    enqueue: bool = True,
) -> list[tuple[Orphan, int | None]]:
    """Scan + finalize + (optionally) enqueue every orphan. Returns (orphan, job_id) pairs."""
    q = queue or (JobQueue() if enqueue else None)
    out: list[tuple[Orphan, int | None]] = []
    for o in find_orphans():
        job_id = finalize_orphan(o, queue=q, enqueue=enqueue)
        out.append((o, job_id))
    return out
