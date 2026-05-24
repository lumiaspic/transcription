"""Audio device discovery for Windows WASAPI."""

from __future__ import annotations

from dataclasses import dataclass

import soundcard as sc


@dataclass
class DeviceInfo:
    name: str
    kind: str  # "mic" or "speaker"
    is_default: bool


def list_devices() -> list[DeviceInfo]:
    out: list[DeviceInfo] = []
    default_mic = sc.default_microphone().name
    default_speaker = sc.default_speaker().name
    for m in sc.all_microphones(include_loopback=False):
        out.append(DeviceInfo(m.name, "mic", m.name == default_mic))
    for s in sc.all_speakers():
        out.append(DeviceInfo(s.name, "speaker", s.name == default_speaker))
    return out


def get_mic(name: str | None = None):
    if name is None:
        return sc.default_microphone()
    return sc.get_microphone(name, include_loopback=False)


def get_system_loopback(speaker_name: str | None = None):
    """Returns a 'microphone' object that captures the speaker's output."""
    if speaker_name is None:
        speaker_name = sc.default_speaker().name
    return sc.get_microphone(speaker_name, include_loopback=True)
