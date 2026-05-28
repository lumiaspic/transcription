"""Orchestration: run a backend over a recording's tracks, write outputs + merge.

Called by both the synchronous `transcribe` CLI command AND the background
worker, so there is exactly one place where the per-track logic lives.

The orchestrator supports two reliability features so that a multi-hour
remote-API session doesn't end with half-done state on the floor:

  - Fallback chain: each track is tried on every backend in `backends` in
    order. A recoverable error (quota / rate-limit / payload-too-large) on
    one backend lets the next take over for *that track only*. The other
    tracks may end up handled by different backends.
  - Per-track resume: if a track's `.json` already exists and is newer than
    its source audio, it's loaded back from disk instead of re-transcribed.
    `force=True` overrides this when the user wants a fresh run.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from ..backends.base import DiarizationUnavailable, TranscriptionBackend, TranscriptResult
from ..backends.remote_openai_compat import RecoverableRemoteAPIError
from ..export.format import load_json, write_all
from ..export.merge import merge_to_markdown
from .speakers import SpeakerProfile, profile_for_track

log = logging.getLogger(__name__)

ProgressCb = Callable[[str], None] | None

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


def _existing_track_result(rec_dir: Path, track: str, audio_path: Path) -> TranscriptResult | None:
    """Return a cached TranscriptResult if `<rec_dir>/<track>.json` exists and
    is at least as new as the source audio, else None.

    The mtime check guards against the case where the user re-recorded a
    track (audio bumped) but the old transcript is still on disk."""
    json_path = rec_dir / f"{track}.json"
    if not json_path.exists():
        return None
    try:
        if json_path.stat().st_mtime < audio_path.stat().st_mtime:
            return None
    except OSError:
        return None
    try:
        return load_json(json_path)
    except (ValueError, KeyError, OSError) as e:
        log.warning("Stale or malformed %s; re-transcribing (%s)", json_path.name, e)
        return None


def _transcribe_one_track(
    track: str,
    audio_path: Path,
    backends: Sequence[TranscriptionBackend],
    *,
    language: str | None,
    diarize: bool,
    progress: ProgressCb,
) -> TranscriptResult:
    """Run the fallback chain for a single track until one backend succeeds.

    Recoverable errors (quota / payload-too-large / rate-limited) advance to
    the next backend. The first terminal error (auth, bad request, anything
    else) is re-raised. If every backend hits a recoverable error, the last
    one is re-raised so the user sees the final wall they hit."""
    profile = profile_for_track(track)
    if not diarize and profile == SpeakerProfile.MULTI:
        profile = SpeakerProfile.SOLO

    last_recoverable: Exception | None = None
    for i, backend in enumerate(backends):
        attempt = f"[{i + 1}/{len(backends)} {backend.name}]"
        if progress:
            progress(f"Transcribing {audio_path.name} {attempt} (profile={profile.value})")
        t0 = time.time()
        try:
            result = backend.transcribe(audio_path, profile=profile, language=language)
        except DiarizationUnavailable as e:
            log.warning("Diarization unavailable on %s, falling back to SOLO: %s", track, e)
            if progress:
                progress(f"  diarization unavailable, retrying as SOLO: {e}")
            result = backend.transcribe(audio_path, profile=SpeakerProfile.SOLO, language=language)
        except RecoverableRemoteAPIError as e:
            last_recoverable = e
            log.warning(
                "Backend %s recoverable error on %s: %s — trying next in chain",
                backend.name,
                track,
                e,
            )
            if progress:
                progress(f"  {backend.name} failed ({type(e).__name__}); trying next backend")
            continue

        result.track = track
        elapsed = time.time() - t0
        rtf = result.duration / elapsed if elapsed > 0 else 0.0
        log.info(
            "Track %s on %s: %d segments, %.1fs audio, %.1fs wall (%.1fx RT)",
            track,
            backend.name,
            len(result.segments),
            result.duration,
            elapsed,
            rtf,
        )
        if progress:
            progress(
                f"  {track}: {len(result.segments)} segments, {rtf:.1f}x realtime "
                f"(via {backend.name})"
            )
        return result

    # Exhausted the chain. The final recoverable error is the most actionable
    # one to surface — re-raising the very first would hide the fact that
    # everything else also said "no".
    assert last_recoverable is not None
    raise last_recoverable


def run_transcription(
    rec_dir: Path,
    backend: TranscriptionBackend | Sequence[TranscriptionBackend],
    *,
    language: str | None = None,
    diarize: bool = True,
    force: bool = False,
    progress: ProgressCb = None,
) -> list[TranscriptResult]:
    """Transcribe all known tracks in a recording directory.

    - Reads `mic.{flac,wav}` / `system.{flac,wav}` if they exist.
    - For each track:
        * Skip with a cached result if `<track>.json` is newer than the
          source audio and `force=False`.
        * Otherwise try each backend in `backend` in order; recoverable
          remote-API errors advance to the next backend.
    - Writes per-track `.txt` / `.srt` / `.json`.
    - Writes merged `transcript.md`.
    - Updates `meta.json` with transcription status.

    `backend` may be a single TranscriptionBackend (legacy callers) or a
    sequence; a single backend is treated as a one-entry chain.

    Raises:
        FileNotFoundError: if rec_dir or both audio files are missing.
    """
    if not rec_dir.exists():
        raise FileNotFoundError(f"Recording dir not found: {rec_dir}")

    backends: tuple[TranscriptionBackend, ...]
    if isinstance(backend, TranscriptionBackend):
        backends = (backend,)
    else:
        backends = tuple(backend)
        if not backends:
            raise ValueError("backend chain is empty")

    track_files: list[tuple[str, Path]] = []
    for t in KNOWN_TRACKS:
        p = find_track_file(rec_dir, t)
        if p is not None:
            track_files.append((t, p))
    if not track_files:
        exts = "|".join(TRACK_EXTS)
        raise FileNotFoundError(f"No {{{'|'.join(KNOWN_TRACKS)}}}.{{{exts}}} in {rec_dir}")

    results: list[TranscriptResult] = []
    for track, audio_path in track_files:
        if not force:
            cached = _existing_track_result(rec_dir, track, audio_path)
            if cached is not None:
                if progress:
                    progress(
                        f"Skipping {track}: existing {track}.json is newer than "
                        f"{audio_path.name} (use --force to redo)"
                    )
                log.info("Resuming %s from cached %s.json", track, track)
                results.append(cached)
                continue

        result = _transcribe_one_track(
            track,
            audio_path,
            backends,
            language=language,
            diarize=diarize,
            progress=progress,
        )
        write_all(result, rec_dir, stem=track)
        results.append(result)

    merge_to_markdown(results, rec_dir / "transcript.md")

    meta = _read_meta(rec_dir)
    meta["transcribed"] = True
    meta["transcribed_at"] = dt.datetime.now().isoformat(timespec="seconds")
    # Record per-track backend so the recording's audit trail reflects what
    # actually happened, even when the chain mixed providers. The legacy
    # single-backend fields are kept for callers that only read those.
    meta["backend"] = backends[0].name
    meta["model"] = results[0].model if results else None
    meta["per_track_backend"] = {r.track: r.backend for r in results}
    meta["per_track_model"] = {r.track: r.model for r in results}
    _write_meta(rec_dir, meta)

    return results
