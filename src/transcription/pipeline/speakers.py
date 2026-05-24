"""Speaker handling profile per audio track."""

from __future__ import annotations

from enum import Enum


class SpeakerProfile(str, Enum):
    """How to handle speaker assignment for a given track."""

    SOLO = "solo"  # Single known speaker (e.g. own mic). No diarization, all -> SPEAKER_00.
    MULTI = "multi"  # Unknown speakers (e.g. system audio with Discord/Teams). Run diarization.


# Convention: filenames map to profiles. Used by the CLI to apply the right
# profile per track without asking the user.
PROFILE_BY_TRACK_NAME: dict[str, SpeakerProfile] = {
    "mic": SpeakerProfile.SOLO,
    "system": SpeakerProfile.MULTI,
}


def profile_for_track(track_name: str) -> SpeakerProfile:
    """Return the speaker profile for a track stem name (e.g. 'mic', 'system')."""
    return PROFILE_BY_TRACK_NAME.get(track_name, SpeakerProfile.MULTI)


def namespace_speaker(track: str, profile: SpeakerProfile, raw_label: str | None) -> str:
    """Convert a raw per-track speaker label into a globally unique label.

    Pyannote produces labels like SPEAKER_00 / SPEAKER_01 that are only
    meaningful relative to the file it analyzed. Without namespacing, the
    merged transcript would show MIC's SPEAKER_00 and SYSTEM's SPEAKER_00
    as if they were the same person -- a real source of confusion.

    Examples:
        ("mic",    SOLO,  "SPEAKER_00") -> "MIC"
        ("system", MULTI, "SPEAKER_00") -> "SYSTEM_S0"
        ("system", MULTI, "SPEAKER_01") -> "SYSTEM_S1"
        ("system", MULTI, None)         -> "SYSTEM"
    """
    track_upper = (track or "?").upper()
    if profile == SpeakerProfile.SOLO:
        return track_upper
    if not raw_label:
        return track_upper
    # Pyannote labels: "SPEAKER_00", "SPEAKER_01"... Compact to "S0", "S1"...
    suffix = raw_label.rsplit("_", 1)[-1].lstrip("0") or "0"
    return f"{track_upper}_S{suffix}"
