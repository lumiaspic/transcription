"""Orchestration: run a backend over a recording's tracks, write outputs + merge.

Called by both the synchronous `transcribe` CLI command AND the background
worker, so there is exactly one place where the per-track logic lives.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import time
from pathlib import Path
from typing import Callable, Optional

from ..backends.base import DiarizationUnavailable, TranscriptResult, TranscriptionBackend
from ..export.format import write_all
from ..export.merge import merge_to_markdown
from .speakers import SpeakerProfile, profile_for_track

log = logging.getLogger(__name__)

ProgressCb = Optional[Callable[[str], None]]

KNOWN_TRACKS = ("mic", "system")
# Audio extensions we look for, in priority order. New recordings are FLAC;
# older recordings are WAV and stay readable.
TRACK_EXTS = ("flac", "wav")


def find_track_file(rec_dir: Path, track: str) -> Path | None:
    """Return the audio file for `track` in `rec_dir`, or None if missing.

    Looks for FLAC first (new format), then WAV (legacy recordings).
    """
    for ext in TRACK_EXTS:
        p = rec_dir / f"{track}.{ext}"
        if p.exists():
            return p
    return None


def _read_meta(rec_dir: Path) -> dict:
    p = rec_dir / "meta.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _write_meta(rec_dir: Path, meta: dict) -> None:
    (rec_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def run_transcription(
    rec_dir: Path,
    backend: TranscriptionBackend,
    *,
    language: str | None = None,
    diarize: bool = True,
    progress: ProgressCb = None,
) -> list[TranscriptResult]:
    """Transcribe all known tracks in a recording directory.

    - Reads `mic.{flac,wav}` / `system.{flac,wav}` if they exist.
    - Writes per-track `.txt` / `.srt` / `.json`.
    - Writes merged `transcript.md`.
    - Updates `meta.json` with transcription status.

    Raises:
        FileNotFoundError: if rec_dir or both wavs are missing.
    """
    if not rec_dir.exists():
        raise FileNotFoundError(f"Recording dir not found: {rec_dir}")

    track_files: list[tuple[str, Path]] = []
    for t in KNOWN_TRACKS:
        p = find_track_file(rec_dir, t)
        if p is not None:
            track_files.append((t, p))
    if not track_files:
        exts = "|".join(TRACK_EXTS)
        raise FileNotFoundError(
            f"No {{{'|'.join(KNOWN_TRACKS)}}}.{{{exts}}} in {rec_dir}"
        )

    results: list[TranscriptResult] = []
    for track, audio_path in track_files:
        profile = profile_for_track(track)
        if not diarize and profile == SpeakerProfile.MULTI:
            profile = SpeakerProfile.SOLO

        if progress:
            progress(f"Transcribing {audio_path.name} (profile={profile.value})")
        t0 = time.time()
        try:
            result = backend.transcribe(audio_path, profile=profile, language=language)
        except DiarizationUnavailable as e:
            log.warning("Diarization unavailable on %s, falling back to SOLO: %s", track, e)
            if progress:
                progress(f"  diarization unavailable, retrying as SOLO: {e}")
            result = backend.transcribe(wav, profile=SpeakerProfile.SOLO, language=language)

        result.track = track
        elapsed = time.time() - t0
        rtf = result.duration / elapsed if elapsed > 0 else 0.0
        log.info(
            "Track %s: %d segments, %.1fs audio, %.1fs wall (%.1fx RT)",
            track, len(result.segments), result.duration, elapsed, rtf,
        )
        if progress:
            progress(f"  {track}: {len(result.segments)} segments, {rtf:.1f}x realtime")
        write_all(result, rec_dir, stem=track)
        results.append(result)

    merge_to_markdown(results, rec_dir / "transcript.md")

    meta = _read_meta(rec_dir)
    meta["transcribed"] = True
    meta["transcribed_at"] = dt.datetime.now().isoformat(timespec="seconds")
    meta["backend"] = backend.name
    meta["model"] = results[0].model if results else None
    _write_meta(rec_dir, meta)

    return results
