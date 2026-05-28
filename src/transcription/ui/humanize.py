"""User-facing formatting helpers for the GUI.

The UI shouldn't expose raw seconds (`11330.5s`) or stamp IDs
(`20260528_121627`); these helpers translate them into something a
non-technical user can scan.

Pure functions, no NiceGUI imports — easy to unit-test.
"""

from __future__ import annotations

import datetime as dt
import re

_REC_ID_RE = re.compile(r"^(\d{8})_(\d{6})$")


def format_duration(seconds: float | int | None) -> str:
    """Format a duration in seconds as a short human-readable string.

    Examples:
      0     -> "0s"
      6.3   -> "6s"
      63    -> "1m 03s"
      125   -> "2m 05s"
      3725  -> "1h 02m"
      11330 -> "3h 08m"

    Hours-resolution drops the seconds — at that scale they're noise.
    None / negative / non-finite values render as a dash.
    """
    if seconds is None:
        return "—"
    try:
        s_float = float(seconds)
    except (TypeError, ValueError):
        return "—"
    if s_float < 0 or s_float != s_float:  # NaN comparison
        return "—"
    s = int(round(s_float))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {sec:02d}s"
    return f"{sec}s"


def parse_recording_id(rec_id: str) -> dt.datetime | None:
    """Parse a `YYYYMMDD_HHMMSS` recording ID into a naive datetime, or None."""
    if not rec_id:
        return None
    m = _REC_ID_RE.match(rec_id)
    if not m:
        return None
    try:
        return dt.datetime.strptime(rec_id, "%Y%m%d_%H%M%S")
    except ValueError:
        return None


def humanize_recording_id(rec_id: str, *, now: dt.datetime | None = None) -> str:
    """Render a recording ID as a friendly date.

    Falls back to the raw ID if it doesn't match the expected pattern,
    so callers can use this unconditionally.

    Examples (relative to 2026-05-28 14:00):
      20260528_121627 -> "Today at 12:16"
      20260527_090403 -> "Yesterday at 09:04"
      20260524_180000 -> "Sun at 18:00"        (within last 7 days)
      20260501_103000 -> "May 1 at 10:30"      (same year)
      20251201_090000 -> "Dec 1, 2025 at 09:00"
    """
    parsed = parse_recording_id(rec_id)
    if parsed is None:
        return rec_id
    today = (now or dt.datetime.now()).date()
    delta_days = (today - parsed.date()).days
    time_str = parsed.strftime("%H:%M")
    if delta_days == 0:
        return f"Today at {time_str}"
    if delta_days == 1:
        return f"Yesterday at {time_str}"
    if 2 <= delta_days <= 6:
        return f"{parsed.strftime('%a')} at {time_str}"
    if parsed.year == today.year:
        return f"{parsed.strftime('%b')} {parsed.day} at {time_str}"
    return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year} at {time_str}"


# Patterns mapped to short user-facing labels. The full traceback stays
# available in the dialog — this is just for the cell preview.
_ERROR_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"CUDA out of memory", re.I), "Graphics card memory exhausted"),
    (re.compile(r"out of memory", re.I), "Out of memory"),
    (re.compile(r"FileNotFoundError|No such file", re.I), "File not found"),
    (re.compile(r"PermissionError|Permission denied", re.I), "Permission denied"),
    (
        re.compile(r"ConnectionError|Connection refused|Failed to establish", re.I),
        "Could not reach the server",
    ),
    (re.compile(r"Timeout|timed out", re.I), "Request timed out"),
    (re.compile(r"401|Unauthorized|Invalid API key", re.I), "Invalid API key"),
    (re.compile(r"403|Forbidden", re.I), "Access forbidden by the server"),
    (re.compile(r"429|rate limit", re.I), "Rate limit reached — try again later"),
    (re.compile(r"5\d\d\b|Internal Server Error|Bad Gateway", re.I), "Remote server error"),
    (re.compile(r"ffmpeg", re.I), "Audio conversion failed"),
    (re.compile(r"diariz", re.I), "Speaker diarization failed"),
    (re.compile(r"CUDA|cuDNN|cublas", re.I), "GPU error"),
)


def humanize_error(err_text: str | None) -> str:
    """Map a raw exception/traceback string to a short human label.

    Falls back to the first line of the input if no pattern matches.
    """
    if not err_text:
        return ""
    for pattern, label in _ERROR_PATTERNS:
        if pattern.search(err_text):
            return label
    # Fallback: first non-empty line, trimmed.
    for raw in err_text.splitlines():
        line = raw.strip()
        if line:
            return line[:80] + ("…" if len(line) > 80 else "")
    return err_text.strip()[:80]
