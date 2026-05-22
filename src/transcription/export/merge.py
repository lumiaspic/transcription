"""Merge multiple TranscriptResults (mic + system) into a single chronological transcript."""
from __future__ import annotations

from pathlib import Path

from ..backends.base import TranscriptResult


def merge_to_markdown(results: list[TranscriptResult], path: Path) -> None:
    """Produces a Markdown transcript with all segments ordered by start time.

    Each segment is tagged with its source track and (if present) speaker:
        **[MIC | SPEAKER_00]** 0:12 -> 0:18
        > what was said

        **[SYSTEM | SPEAKER_01]** 0:15 -> 0:22
        > what was said on the other side
    """
    # Flatten: (start, end, source, speaker, text)
    rows = []
    for r in results:
        for s in r.segments:
            rows.append((s.start, s.end, r.track.upper() or "?", s.speaker, s.text))
    rows.sort(key=lambda x: x[0])

    def fmt(t: float) -> str:
        m, sec = divmod(int(round(t)), 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"

    lines = ["# Transcript", ""]
    if results:
        lines.append(f"_Language: {results[0].language}, model: {results[0].model}, backend: {results[0].backend}_")
        lines.append("")
    for start, end, source, speaker, text in rows:
        tag = f"{source}" + (f" | {speaker}" if speaker else "")
        lines.append(f"**[{tag}]** `{fmt(start)} → {fmt(end)}`")
        lines.append(f"> {text}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
