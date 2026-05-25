"""Audio device discovery (WASAPI on Windows, CoreAudio on macOS)."""

from __future__ import annotations

import sys
from dataclasses import dataclass

import soundcard as sc

# Substrings (case-insensitive) we recognize as virtual loopback drivers on
# macOS. CoreAudio has no native loopback, so the user routes their system
# output through one of these so we can record it as a microphone.
_MAC_LOOPBACK_HINTS = ("blackhole", "loopback", "soundflower", "ladiocast", "vb-cable")


class SystemLoopbackUnavailable(RuntimeError):
    """Raised when no system-audio loopback path is usable on this machine."""


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
    """Returns a 'microphone' object that captures the speaker's output.

    Windows: uses WASAPI loopback on the speaker device — works on any output.
    macOS:   CoreAudio has no loopback. Requires a virtual audio device
             (BlackHole etc.) routed via an Aggregate Device. We pick the
             first virtual loopback device we find, or honour an explicit
             ``speaker_name`` if it matches a microphone.
    """
    if sys.platform == "darwin":
        return _get_mac_system_loopback(speaker_name)

    # Windows + Linux (PulseAudio monitor sources) path.
    if speaker_name is None:
        speaker_name = sc.default_speaker().name
    return sc.get_microphone(speaker_name, include_loopback=True)


def _get_mac_system_loopback(speaker_name: str | None):
    mics = sc.all_microphones(include_loopback=False)

    # User explicitly passed a name (e.g. "BlackHole 2ch") — honour it
    # verbatim, treating it as a microphone (which is how virtual loopback
    # drivers register on macOS).
    if speaker_name:
        try:
            return sc.get_microphone(speaker_name, include_loopback=False)
        except (IndexError, RuntimeError) as e:
            raise SystemLoopbackUnavailable(
                f"No microphone named {speaker_name!r}. On macOS, pass the name "
                "of a virtual audio device (e.g. 'BlackHole 2ch') as --speaker, "
                "not a hardware speaker — CoreAudio has no native loopback."
            ) from e

    # Auto-detect a known virtual loopback driver in the mic list.
    for mic in mics:
        if any(hint in mic.name.lower() for hint in _MAC_LOOPBACK_HINTS):
            return mic

    raise SystemLoopbackUnavailable(
        "System audio capture on macOS requires a virtual audio device. "
        "Install BlackHole (`brew install --cask blackhole-2ch`), then in "
        "Audio MIDI Setup create a Multi-Output Device (NOT an Aggregate "
        "Device) combining your speakers and BlackHole, and select it as "
        "your system output. Pass --speaker 'BlackHole 2ch' explicitly or "
        "rely on auto-detection."
    )
