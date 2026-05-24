"""Tests for `pipeline.speakers` — speaker profile + namespace logic.

This module is pure (no I/O, no global state), so tests are simple
parametrized cases. Good first test target.
"""

from __future__ import annotations

import pytest

from transcription.pipeline.speakers import (
    PROFILE_BY_TRACK_NAME,
    SpeakerProfile,
    namespace_speaker,
    profile_for_track,
)


class TestProfileForTrack:
    """`profile_for_track` is a simple lookup with a sensible default."""

    @pytest.mark.parametrize(
        ("track", "expected"),
        [
            ("mic", SpeakerProfile.SOLO),
            ("system", SpeakerProfile.MULTI),
        ],
    )
    def test_known_tracks_use_their_mapped_profile(
        self, track: str, expected: SpeakerProfile
    ) -> None:
        assert profile_for_track(track) is expected

    @pytest.mark.parametrize("unknown", ["", "unknown_track", "MIC", "système"])
    def test_unknown_track_falls_back_to_multi(self, unknown: str) -> None:
        # The safer default: assume an unknown track might have multiple
        # speakers, so we still attempt diarization rather than collapsing
        # everyone into SPEAKER_00.
        assert profile_for_track(unknown) is SpeakerProfile.MULTI

    def test_mapping_constant_stays_in_sync_with_function(self) -> None:
        # Guards against someone editing PROFILE_BY_TRACK_NAME without
        # updating the function's behavior (and vice-versa).
        for track, profile in PROFILE_BY_TRACK_NAME.items():
            assert profile_for_track(track) is profile


class TestNamespaceSpeaker:
    """Per the module's own docstring examples."""

    def test_solo_collapses_to_track_name_uppercased(self) -> None:
        assert namespace_speaker("mic", SpeakerProfile.SOLO, "SPEAKER_00") == "MIC"

    def test_solo_ignores_raw_label_entirely(self) -> None:
        # SOLO profile means we don't care what diarization said.
        assert namespace_speaker("mic", SpeakerProfile.SOLO, "SPEAKER_42") == "MIC"
        assert namespace_speaker("mic", SpeakerProfile.SOLO, None) == "MIC"

    @pytest.mark.parametrize(
        ("raw_label", "expected"),
        [
            ("SPEAKER_00", "SYSTEM_S0"),
            ("SPEAKER_01", "SYSTEM_S1"),
            ("SPEAKER_10", "SYSTEM_S10"),
            ("SPEAKER_00000", "SYSTEM_S0"),  # padding zeros stripped
        ],
    )
    def test_multi_strips_speaker_prefix_and_leading_zeros(
        self, raw_label: str, expected: str
    ) -> None:
        assert namespace_speaker("system", SpeakerProfile.MULTI, raw_label) == expected

    def test_multi_without_raw_label_falls_back_to_track_name(self) -> None:
        # Happens when a segment has no speaker info (e.g. silence at start
        # of file, or diarization model returning None).
        assert namespace_speaker("system", SpeakerProfile.MULTI, None) == "SYSTEM"
        assert namespace_speaker("system", SpeakerProfile.MULTI, "") == "SYSTEM"

    def test_empty_track_name_uses_question_mark_placeholder(self) -> None:
        # Defensive: the caller forgot to populate `track` on the result.
        # Better to write "?_S0" in the transcript than to crash mid-export.
        assert namespace_speaker("", SpeakerProfile.MULTI, "SPEAKER_00") == "?_S0"
