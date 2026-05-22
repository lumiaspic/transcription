"""Speaker handling profile per audio track."""
from __future__ import annotations

from enum import Enum


class SpeakerProfile(str, Enum):
    """How to handle speaker assignment for a given track."""

    SOLO = "solo"     # Single known speaker (e.g. own mic). No diarization, all -> SPEAKER_00.
    MULTI = "multi"   # Unknown speakers (e.g. system audio with Discord/Teams). Run diarization.


# Convention: filenames map to profiles. Used by the CLI to apply the right
# profile per track without asking the user.
PROFILE_BY_TRACK_NAME: dict[str, SpeakerProfile] = {
    "mic": SpeakerProfile.SOLO,
    "system": SpeakerProfile.MULTI,
}


def profile_for_track(track_name: str) -> SpeakerProfile:
    """Return the speaker profile for a track stem name (e.g. 'mic', 'system')."""
    return PROFILE_BY_TRACK_NAME.get(track_name, SpeakerProfile.MULTI)
