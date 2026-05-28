"""Merge multiple TranscriptResults (mic + system) into a single chronological transcript."""

from __future__ import annotations

from pathlib import Path

from ..backends.base import TranscriptResult
from ..pipeline.speakers import namespace_speaker


def merge_to_markdown(results: list[TranscriptResult], path: Path) -> None:
    """Produces a Markdown transcript with all segments ordered by start time.

    Speaker labels are globally namespaced so MIC's speaker is never confused
    with a SYSTEM speaker:
        **[MIC]** 0:12 -> 0:18
        > what the user said

        **[SYSTEM_S0]** 0:15 -> 0:22         (interviewee)
        > ...
        **[SYSTEM_S1]** 0:22 -> 0:25         (interviewer)
        > ...
    """
    # Flatten: (start, end, namespaced_speaker, text)
    rows = []
    for r in results:
        for s in r.segments:
            label = namespace_speaker(r.track, r.profile, s.speaker)
            rows.append((s.start, s.end, label, s.text))
    rows.sort(key=lambda x: x[0])

    def fmt(t: float) -> str:
        m, sec = divmod(int(round(t)), 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"

    lines = ["# Transcript", ""]
    if results:
        # If every track was handled by the same backend/model we keep the
        # original compact one-liner. When the fallback chain mixed providers
        # we break the metadata out per-track so the reader knows which
        # segment came from where (e.g. mic via Groq, system via OpenAI).
        backends = {r.backend for r in results}
        models = {r.model for r in results}
        if len(backends) == 1 and len(models) == 1:
            lines.append(
                f"_Language: {results[0].language}, model: {results[0].model}, "
                f"backend: {results[0].backend}_"
            )
        else:
            lines.append(f"_Language: {results[0].language}_")
            lines.append("")
            lines.append("_Per-track backend:_")
            for r in results:
                lines.append(f"- `{r.track}`: {r.backend} / {r.model}")
        # Speaker legend so the reader knows what each label maps to.
        unique = sorted({label for _, _, label, _ in rows})
        if unique:
            lines.append("")
            lines.append("_Speakers: " + ", ".join(f"`{u}`" for u in unique) + "_")
        lines.append("")

    for start, end, speaker, text in rows:
        lines.append(f"**[{speaker}]** `{fmt(start)} → {fmt(end)}`")
        lines.append(f"> {text}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
