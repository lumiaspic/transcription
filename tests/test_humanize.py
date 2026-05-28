"""Tests for the UI-facing formatting helpers."""

from __future__ import annotations

import datetime as dt

import pytest

from transcription.ui.humanize import (
    format_duration,
    humanize_error,
    humanize_recording_id,
    parse_recording_id,
)


class TestFormatDuration:
    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [
            (0, "0s"),
            (1, "1s"),
            (6.3, "6s"),
            (59, "59s"),
            (60, "1m 00s"),
            (63, "1m 03s"),
            (125, "2m 05s"),
            (3600, "1h 00m"),
            (3725, "1h 02m"),
            (11330.5, "3h 08m"),
        ],
    )
    def test_typical_values(self, seconds: float, expected: str) -> None:
        assert format_duration(seconds) == expected

    @pytest.mark.parametrize("bad", [None, -1, -0.5, float("nan")])
    def test_invalid_inputs_render_as_dash(self, bad: object) -> None:
        assert format_duration(bad) == "—"  # type: ignore[arg-type]

    def test_string_passes_through_float(self) -> None:
        # In meta.json a sloppy producer might emit duration as a string.
        # Be lenient.
        assert format_duration("125") == "2m 05s"  # type: ignore[arg-type]

    def test_garbage_string_returns_dash(self) -> None:
        assert format_duration("nope") == "—"  # type: ignore[arg-type]


class TestParseRecordingId:
    def test_parses_valid_id(self) -> None:
        assert parse_recording_id("20260528_121627") == dt.datetime(2026, 5, 28, 12, 16, 27)

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "not-an-id",
            "20260528-121627",  # wrong separator
            "20261301_000000",  # invalid month
            "20260528_256099",  # invalid time
        ],
    )
    def test_rejects_invalid(self, bad: str) -> None:
        assert parse_recording_id(bad) is None


class TestHumanizeRecordingId:
    _NOW = dt.datetime(2026, 5, 28, 14, 0, 0)

    def test_today(self) -> None:
        assert humanize_recording_id("20260528_121627", now=self._NOW) == "Today at 12:16"

    def test_yesterday(self) -> None:
        assert humanize_recording_id("20260527_090403", now=self._NOW) == "Yesterday at 09:04"

    def test_within_last_week_uses_weekday(self) -> None:
        # 2026-05-24 is a Sunday.
        out = humanize_recording_id("20260524_180000", now=self._NOW)
        assert out == "Sun at 18:00"

    def test_same_year(self) -> None:
        assert humanize_recording_id("20260501_103000", now=self._NOW) == "May 1 at 10:30"

    def test_previous_year(self) -> None:
        assert humanize_recording_id("20251201_090000", now=self._NOW) == "Dec 1, 2025 at 09:00"

    def test_unparseable_id_is_passed_through(self) -> None:
        assert humanize_recording_id("custom-name", now=self._NOW) == "custom-name"


class TestHumanizeError:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (
                "RuntimeError: CUDA out of memory. Tried to allocate 2.00 GiB",
                "Graphics card memory exhausted",
            ),
            ("FileNotFoundError(2, 'No such file or directory')", "File not found"),
            ("PermissionError: [Errno 13] Permission denied: '/foo'", "Permission denied"),
            (
                "requests.exceptions.ConnectionError: Connection refused",
                "Could not reach the server",
            ),
            ("TimeoutError: Request timed out after 600s", "Request timed out"),
            ("openai.AuthenticationError: 401 Unauthorized", "Invalid API key"),
            ("HTTP 429: rate limit exceeded", "Rate limit reached — try again later"),
            ("HTTP 502 Bad Gateway", "Remote server error"),
            ("ffmpeg exited with non-zero status", "Audio conversion failed"),
        ],
    )
    def test_known_patterns(self, raw: str, expected: str) -> None:
        assert humanize_error(raw) == expected

    def test_unknown_falls_back_to_first_line(self) -> None:
        msg = "Traceback (most recent call last):\n  File ...\nValueError: weird thing"
        assert humanize_error(msg) == "Traceback (most recent call last):"

    def test_empty_input(self) -> None:
        assert humanize_error(None) == ""
        assert humanize_error("") == ""

    def test_long_fallback_is_truncated(self) -> None:
        msg = "X" * 200
        out = humanize_error(msg)
        assert out.endswith("…")
        assert len(out) <= 81  # 80 chars + ellipsis
