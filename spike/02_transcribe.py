"""
Spike 02 - Validate WhisperX runs locally on this machine.

Goal:
- Detect GPU availability (we expect CUDA on the gaming PC, CPU on the work PC).
- Transcribe a WAV file with WhisperX and print the segments.
- Measure realtime factor (e.g. "5.2x realtime" means 1 min audio took ~12 s).

Run:
    uv run --extra transcribe python spike/02_transcribe.py recordings/spike/mic.wav
    uv run --extra transcribe python spike/02_transcribe.py recordings/spike/system.wav --model medium
    uv run --extra transcribe python spike/02_transcribe.py path/to/audio.wav --model large-v3

Models: tiny | base | small | medium | large-v3
On RTX 3070 (8 GB), large-v3 in float16 fits comfortably.
On CPU, prefer small or medium with int8.

NOTE: this spike SKIPS diarization (which requires a HuggingFace token +
accepting pyannote/speaker-diarization-3.1 + pyannote/segmentation-3.0 EULAs).
Diarization is validated in spike 03 once transcription is confirmed working.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import torch
import whisperx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path)
    parser.add_argument("--model", default="small")
    parser.add_argument("--language", default=None, help="auto-detect if omitted")
    args = parser.parse_args()

    if not args.audio.exists():
        sys.exit(f"File not found: {args.audio}")

    has_cuda = torch.cuda.is_available()
    device = "cuda" if has_cuda else "cpu"
    compute_type = "float16" if has_cuda else "int8"
    print(f"Device       : {device}  (compute_type={compute_type})")
    if has_cuda:
        print(f"GPU          : {torch.cuda.get_device_name(0)}")
        vram_gb = torch.cuda.get_device_properties(0).total_memory / 1024**3
        print(f"VRAM         : {vram_gb:.1f} GB")
    print(f"Model        : {args.model}")
    print(f"Audio        : {args.audio}")
    print()

    t0 = time.time()
    model = whisperx.load_model(args.model, device, compute_type=compute_type)
    print(f"Model loaded in {time.time() - t0:.1f}s")

    audio = whisperx.load_audio(str(args.audio))
    audio_duration = len(audio) / 16_000  # whisperx loads at 16 kHz internally

    t0 = time.time()
    result = model.transcribe(audio, language=args.language, batch_size=16)
    elapsed = time.time() - t0
    print(f"Transcribed in {elapsed:.1f}s  ({audio_duration / elapsed:.1f}x realtime)")
    print(f"Detected language: {result.get('language', '?')}")
    print()
    print("=== Segments ===")
    for seg in result["segments"]:
        print(f"[{seg['start']:7.2f} -> {seg['end']:7.2f}]  {seg['text'].strip()}")


if __name__ == "__main__":
    main()
