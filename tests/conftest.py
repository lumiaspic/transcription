"""Shared pytest fixtures.

Lives at the root of `tests/` so it's auto-discovered by all test modules
without needing an explicit import (pytest convention).
"""

from __future__ import annotations

import pytest

from transcription.backends.base import Segment, TranscriptResult
from transcription.pipeline.speakers import SpeakerProfile


def _make_segment(start: float, end: float, text: str, speaker: str | None = None) -> Segment:
    return Segment(start=start, end=end, text=text, speaker=speaker)


@pytest.fixture
def mic_result() -> TranscriptResult:
    """A typical SOLO mic transcript with 2 short segments, no speaker labels."""
    return TranscriptResult(
        language="fr",
        segments=[
            _make_segment(0.0, 2.5, "Bonjour."),
            _make_segment(3.0, 5.2, "Comment ça va ?"),
        ],
        duration=5.2,
        backend="test",
        model="tiny",
        profile=SpeakerProfile.SOLO,
        track="mic",
    )


@pytest.fixture
def system_result() -> TranscriptResult:
    """A MULTI system transcript with 2 diarized speakers."""
    return TranscriptResult(
        language="fr",
        segments=[
            _make_segment(1.0, 2.0, "Salut.", speaker="SPEAKER_00"),
            _make_segment(2.5, 4.0, "Salut, ça va ?", speaker="SPEAKER_01"),
            _make_segment(4.5, 5.0, "Oui.", speaker="SPEAKER_00"),
        ],
        duration=5.0,
        backend="test",
        model="tiny",
        profile=SpeakerProfile.MULTI,
        track="system",
    )
