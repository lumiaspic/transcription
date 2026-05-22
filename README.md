# 🎙️ Transcription

> Local Windows desktop app that replaces the OBS → FFmpeg → Colab → WhisperX workflow with one click.
> Capture mic + system audio as two separate tracks, auto-transcribe with WhisperX + pyannote diarization, get a merged Markdown transcript on disk.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform: Windows 11](https://img.shields.io/badge/platform-Windows%2011-0078D6)](https://www.microsoft.com/windows)
[![Python 3.10–3.12](https://img.shields.io/badge/python-3.10%E2%80%933.12-blue.svg)](https://www.python.org/)

---

## What it does

- Captures the **microphone** and the **system audio** (Discord, Teams, browser, music…) as **two separate tracks** — no mixing.
- Auto-transcribes both with **WhisperX**, runs **pyannote speaker diarization** on the system track.
- Produces per-track `.txt` / `.srt` / `.json` plus a **merged chronological Markdown transcript** with globally-unique speaker labels (`MIC`, `SYSTEM_S0`, `SYSTEM_S1`).
- Runs **entirely on your machine** by default. No upload, no cloud.
- Records **16 kHz FLAC** out of the box — ~100 MB/h for both tracks combined, lossless, matches what Whisper and pyannote both resample to internally.

## Status

Personal prototype, used daily on Windows 11 with an NVIDIA GPU. Works also on CPU-only machines (slower). Built incrementally as a single pair-programming sprint with [Claude Code](https://claude.com/claude-code); the commit history is meant to be readable.

## Install (~10 min, Windows 11)

The full procedure is in [`packaging/README.md`](packaging/README.md). TL;DR:

1. Download [`packaging/install.ps1`](packaging/install.ps1) (private repo → use GitHub's **Raw** button).
2. Open PowerShell where you saved it:
   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass
   .\install.ps1
   ```
   The script installs `git` and `uv` if missing, clones the repo into `%LOCALAPPDATA%\Programs\transcription`, syncs ~3 GB of CUDA wheels, and creates Desktop + Start Menu shortcuts.
3. Set your HuggingFace token (one-time, enables speaker diarization):
   ```powershell
   cd $env:LOCALAPPDATA\Programs\transcription
   uv run transcription config set-token huggingface
   ```
   You also need to accept the licence on <https://huggingface.co/pyannote/speaker-diarization-community-1>.
4. Launch via the **Transcription** Desktop shortcut. The first-run wizard detects your hardware and asks which backend mode to use.

## Usage

### GUI (primary)

```powershell
transcription gui
```

Single native window, three cards:
- **Recording** — Start / Stop buttons + live chrono. Stop auto-enqueues a transcription job.
- **Jobs** — live table of recent jobs; click a `done` row to open the folder, click a `failed` row for the error.
- **Recordings** — 10 most recent + an Open button.

The GUI runs its own worker thread inside the same process — no separate daemon needed.

### CLI (alternative / power user)

```powershell
transcription record --duration 60       # records, auto-enqueues
transcription daemon                      # runs the background worker (CLI-only flow)
transcription jobs list                   # show the queue
transcription transcribe <id>             # synchronous one-off (blocks until done)
transcription doctor                      # health check (Python, CUDA, HF token, jobs DB)
transcription recover                     # finalize recordings interrupted by a crash
transcription config show                 # current settings + token status
```

Full reference: `transcription --help` and `transcription <command> --help`.

## How it works

```
┌─────────────────────────────────────────────────────────────────┐
│                  NiceGUI desktop window                         │
│              Start ▶ │ Stop ■ │ Jobs │ Recordings               │
└────────────┬──────────────────────────┬─────────────────────────┘
             │                          │
   WASAPI capture                  SQLite job queue
   (mic + loopback)                (%APPDATA%\jobs.db, WAL mode)
             │                          │
             ▼                          ▼
   recordings/<id>/                Worker (same process,
     mic.flac                       daemon thread)
     system.flac                    - claims pending jobs atomically
     meta.json                      - keeps backend cached in VRAM
                                    - recovers orphans on startup
                                          │
                                          ▼
                            TranscriptionBackend (interface)
                            └── WhisperXLocalBackend       ← only impl today
                                + faster-whisper (CPU fallback, auto)
                                RemoteAPIBackend            ← stub for v2
                                          │
                                          ▼
                               Per-track outputs
                                 mic.{txt,srt,json}
                                 system.{txt,srt,json}
                                 transcript.md  (chronological merge,
                                                 speakers namespaced
                                                 globally)
```

A few design choices worth knowing about:

- **SQLite is the only IPC** between the GUI and the worker. No sockets, no PID files. `sqlite3 %APPDATA%\transcription\jobs.db` is the debugging tool.
- **Speaker labels are namespaced at merge time**: the mic's `SPEAKER_00` and the system's `SPEAKER_00` are different people, so we relabel them globally (`MIC`, `SYSTEM_S0`, `SYSTEM_S1`) in `transcript.md`. Per-track JSON files keep the raw pyannote labels.
- **Backend selection goes through a single switch** (`backends/factory.py::get_backend`). Adding a remote API impl is one new file plus unblocking one branch in the factory.
- **Packaging uses `uv` over PyInstaller.** The install is a managed git checkout that updates with `git pull`; no 3-4 GB single binary to rebuild for every torch / whisperx version bump. See [`packaging/README.md`](packaging/README.md) for the rationale.
- **Crashed recordings are auto-recovered.** If the PC shuts down mid-record, `transcription recover` (or the next GUI launch) finalizes the audio files into a proper recording + enqueues the job.

## Configuration

Lives in `%APPDATA%\transcription\config.toml`. Tokens (HuggingFace, future API keys) live in Windows Credential Manager via `transcription config set-token <service>` — never in a file.

Most-used keys:

| Key                         | Default      | What it controls                                                                 |
|-----------------------------|--------------|----------------------------------------------------------------------------------|
| `backend_mode`              | (wizard)     | `local_gpu` / `local_cpu` / `remote_api`. Set by the first-run wizard.           |
| `model`                     | `small`      | Whisper model size (`tiny` → `large-v3`).                                        |
| `language`                  | `null`       | ISO code (`fr`, `en`, …) or `null` to auto-detect.                               |
| `recording_format`          | `flac`       | `flac` (recommended, lossless, ~½ of WAV) or `wav`.                              |
| `recording_sample_rate`     | `16000`      | Hz. Whisper / pyannote both resample to 16 kHz internally — higher just wastes disk. |

Per-recording overrides via `transcription record --model medium --sample-rate 48000 --format wav`.

## Tech stack

| | |
|---|---|
| Audio capture        | [soundcard](https://github.com/bastibe/SoundCard) (WASAPI loopback) |
| Transcription        | [WhisperX](https://github.com/m-bain/whisperX) (Whisper + word-level alignment) |
| Speaker diarization  | [pyannote-audio](https://github.com/pyannote/pyannote-audio) (`speaker-diarization-community-1`) |
| CLI                  | [Typer](https://typer.tiangolo.com/) |
| Desktop UI           | [NiceGUI](https://nicegui.io/) + [pywebview](https://pywebview.flowrl.com/) (Edge WebView2 native window on Win11) |
| Env / packaging      | [uv](https://docs.astral.sh/uv/) |
| Job queue            | SQLite (stdlib, WAL mode) |
| Token storage        | [keyring](https://github.com/jaraco/keyring) (Windows Credential Manager) |

## Limitations and what's NOT done

- **Windows only.** Audio capture uses WASAPI loopback; cross-platform would need a different backend (PortAudio / PulseAudio / CoreAudio loopback).
- **System audio is captured as one combined stream.** No per-app separation yet (Discord-only / Teams-only / browser-only). The Windows Process Loopback API would unlock this — planned for v2.
- **No `RemoteAPIBackend` implementation yet.** Wizard exposes the option, factory has the slot, raise `RemoteBackendNotImplemented` for now. To be wired the day a remote endpoint is picked (Replicate, RunPod, or a self-hosted server reached via Tailscale).
- **No real-time transcription.** Recording finalizes first, then the background worker transcribes. Adding streaming would require a different audio chunking + a streaming-capable backend.
- **No CPU-only install variant.** The work PC install pulls the full CUDA wheels (~3 GB). A CPU-only extra in `pyproject.toml` would shrink this to ~500 MB but adds maintenance overhead.

## Acknowledgements

Standing on the shoulders of giants:

- [OpenAI Whisper](https://github.com/openai/whisper) for the base transcription model.
- [WhisperX](https://github.com/m-bain/whisperX) and Max Bain for the alignment + diarization wrapper.
- [pyannote.audio](https://github.com/pyannote/pyannote-audio) and Hervé Bredin for the speaker diarization.
- [Astral](https://astral.sh/) for `uv`, which makes the install actually pleasant.

## License

[MIT](LICENSE).
