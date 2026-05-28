"""Export a TranscriptResult to .txt, .srt, .json."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from ..backends.base import Segment, TranscriptResult
from ..pipeline.speakers import SpeakerProfile


def to_txt(result: TranscriptResult, path: Path) -> None:
    lines = []
    for s in result.segments:
        speaker = f"[{s.speaker}] " if s.speaker else ""
        lines.append(f"{speaker}{s.text}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def to_srt(result: TranscriptResult, path: Path) -> None:
    """Standard SRT subtitle format."""

    def fmt(t: float) -> str:
        ms = int(round(t * 1000))
        h, ms = divmod(ms, 3_600_000)
        m, ms = divmod(ms, 60_000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    chunks = []
    for i, seg in enumerate(result.segments, start=1):
        speaker = f"[{seg.speaker}] " if seg.speaker else ""
        chunks.append(f"{i}\n{fmt(seg.start)} --> {fmt(seg.end)}\n{speaker}{seg.text}\n")
    path.write_text("\n".join(chunks), encoding="utf-8")


def to_json(result: TranscriptResult, path: Path) -> None:
    data = {
        "language": result.language,
        "duration": result.duration,
        "backend": result.backend,
        "model": result.model,
        "profile": result.profile.value,
        "track": result.track,
        "segments": [asdict(s) for s in result.segments],
        "meta": result.meta,
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_json(path: Path) -> TranscriptResult:
    """Reconstruct a TranscriptResult from a per-track `.json` written by
    `to_json`. Used by `run_transcription`'s resume path so a partially
    completed recording doesn't redo work after a crash or quota error."""
    data = json.loads(path.read_text(encoding="utf-8"))
    segments = [
        Segment(
            start=float(s.get("start", 0.0)),
            end=float(s.get("end", 0.0)),
            text=str(s.get("text", "")),
            speaker=s.get("speaker"),
        )
        for s in data.get("segments", [])
    ]
    return TranscriptResult(
        language=str(data.get("language", "unknown")),
        segments=segments,
        duration=float(data.get("duration", 0.0)),
        backend=str(data.get("backend", "unknown")),
        model=str(data.get("model", "unknown")),
        profile=SpeakerProfile(data.get("profile", SpeakerProfile.SOLO.value)),
        track=str(data.get("track", "")),
        meta=dict(data.get("meta", {})),
    )


def write_all(result: TranscriptResult, dir_path: Path, stem: str) -> dict[str, Path]:
    """Write .txt, .srt, .json for one track. Returns the paths."""
    dir_path.mkdir(parents=True, exist_ok=True)
    paths = {
        "txt": dir_path / f"{stem}.txt",
        "srt": dir_path / f"{stem}.srt",
        "json": dir_path / f"{stem}.json",
    }
    to_txt(result, paths["txt"])
    to_srt(result, paths["srt"])
    to_json(result, paths["json"])
    return paths
