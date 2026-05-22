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

    - Reads `mic.wav` / `system.wav` if they exist.
    - Writes per-track `.txt` / `.srt` / `.json`.
    - Writes merged `transcript.md`.
    - Updates `meta.json` with transcription status.

    Raises:
        FileNotFoundError: if rec_dir or both wavs are missing.
    """
    if not rec_dir.exists():
        raise FileNotFoundError(f"Recording dir not found: {rec_dir}")

    tracks = [t for t in KNOWN_TRACKS if (rec_dir / f"{t}.wav").exists()]
    if not tracks:
        raise FileNotFoundError(f"No {'/'.join(f'{t}.wav' for t in KNOWN_TRACKS)} in {rec_dir}")

    results: list[TranscriptResult] = []
    for track in tracks:
        wav = rec_dir / f"{track}.wav"
        profile = profile_for_track(track)
        if not diarize and profile == SpeakerProfile.MULTI:
            profile = SpeakerProfile.SOLO

        if progress:
            progress(f"Transcribing {track}.wav (profile={profile.value})")
        t0 = time.time()
        try:
            result = backend.transcribe(wav, profile=profile, language=language)
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
