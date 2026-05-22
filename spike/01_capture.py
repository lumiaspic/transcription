"""
Spike 01 - Validate Windows WASAPI capture of mic + system loopback in parallel.

Goal:
- Confirm we can capture the default microphone AND the default speaker's
  loopback simultaneously into two separate WAV files.
- Confirm no audio mixing between the two streams.

Run:
    uv run python spike/01_capture.py
    uv run python spike/01_capture.py --duration 30
    uv run python spike/01_capture.py --list   # just list devices and exit

During capture: speak into the mic AND play audio (YouTube, Discord call, etc.)
to verify both streams are truly separate.
"""
from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path

import soundcard as sc
import soundfile as sf

SAMPLE_RATE = 48_000  # native rate of most Windows devices; resample later for Whisper


def _record(recorder_cm, out_path: Path, duration: float, label: str) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with recorder_cm as rec:
        t0 = time.time()
        frames = rec.record(numframes=int(SAMPLE_RATE * duration))
        elapsed = time.time() - t0
    sf.write(str(out_path), frames, SAMPLE_RATE, subtype="PCM_16")
    print(f"  [{label:6}] {len(frames) / SAMPLE_RATE:5.1f}s captured in {elapsed:5.1f}s -> {out_path}")


def list_devices() -> None:
    print("=== Input devices (microphones) ===")
    for m in sc.all_microphones(include_loopback=False):
        marker = " (default)" if m.name == sc.default_microphone().name else ""
        print(f"  - {m.name}{marker}")
    print("\n=== Output devices (speakers) - loopback-capturable ===")
    for s in sc.all_speakers():
        marker = " (default)" if s.name == sc.default_speaker().name else ""
        print(f"  - {s.name}{marker}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--out", type=Path, default=Path("recordings/spike"))
    parser.add_argument("--list", action="store_true", help="List devices and exit")
    args = parser.parse_args()

    if args.list:
        list_devices()
        return

    mic = sc.default_microphone()
    speaker = sc.default_speaker()
    loopback = sc.get_microphone(speaker.name, include_loopback=True)

    print(f"Mic      : {mic.name}")
    print(f"System   : {speaker.name}  (via loopback)")
    print(f"Duration : {args.duration}s @ {SAMPLE_RATE} Hz")
    print(f"Output   : {args.out.resolve()}")
    print()
    print(">>> Speak into your mic AND play some audio (music, video) <<<")
    print()

    mic_rec = mic.recorder(samplerate=SAMPLE_RATE, channels=1)
    loop_rec = loopback.recorder(samplerate=SAMPLE_RATE, channels=2)

    threads = [
        threading.Thread(target=_record, args=(mic_rec, args.out / "mic.wav", args.duration, "mic")),
        threading.Thread(target=_record, args=(loop_rec, args.out / "system.wav", args.duration, "system")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    print(f"\nDone. Play back both files to confirm they contain only their respective sources.")


if __name__ == "__main__":
    main()
