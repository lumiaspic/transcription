# 🎙️ Transcription

> Local desktop app that replaces the OBS → FFmpeg → Colab → WhisperX workflow with one click.
> Capture mic + system audio as two separate tracks, auto-transcribe with WhisperX + pyannote diarization, get a merged Markdown transcript on disk.

[![CI](https://github.com/lumiaspic/transcription/actions/workflows/ci.yml/badge.svg)](https://github.com/lumiaspic/transcription/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/lumiaspic/transcription/graph/badge.svg)](https://codecov.io/gh/lumiaspic/transcription)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform: Windows | macOS](https://img.shields.io/badge/platform-Windows%20%7C%20macOS-blue)](packaging/README.md)
[![Python 3.11–3.12](https://img.shields.io/badge/python-3.11%E2%80%933.12-blue.svg)](https://www.python.org/)

---

## What it does

- Captures the **microphone** and the **system audio** (Discord, Teams, browser, music…) as **two separate tracks** — no mixing.
- Auto-transcribes both with **WhisperX**, runs **pyannote speaker diarization** on the system track.
- Produces per-track `.txt` / `.srt` / `.json` plus a **merged chronological Markdown transcript** with globally-unique speaker labels (`MIC`, `SYSTEM_S0`, `SYSTEM_S1`).
- Runs **entirely on your machine** by default. No upload, no cloud.
- Records **16 kHz FLAC** out of the box — ~100 MB/h for both tracks combined, lossless, matches what Whisper and pyannote both resample to internally.

## Status

Personal prototype, used daily on Windows 11 with an NVIDIA GPU. Works also on CPU-only machines and macOS (Apple Silicon via MPS, Intel via CPU). Built incrementally as a single pair-programming sprint with [Claude Code](https://claude.com/claude-code); the commit history is meant to be readable.

## Install on Windows — no command line needed

### ⬇️ [**Download the installer**](https://raw.githubusercontent.com/lumiaspic/transcription/main/Install-Transcription.bat) (`Install-Transcription.bat`)

Then **3 steps**:

1. **Double-click** the downloaded `Install-Transcription.bat`.
2. Windows may show a blue **"Windows protected your PC"** warning (the script
   isn't code-signed). Click **More info → Run anyway** — it just runs the
   public installer. Keep the window open until it says **Done** (5–10 min the
   first time: it downloads ~3 GB of PyTorch / CUDA wheels).
3. Launch from the **`Transcription`** shortcut now on your Desktop. A first-run
   wizard appears and walks you through the rest (backend choice + token).

That's it. If anything fails, just double-click the `.bat` again — it resumes
where it stopped.

<details>
<summary>Advanced install (PowerShell)</summary>

```powershell
irm https://raw.githubusercontent.com/lumiaspic/transcription/main/packaging/install.ps1 | iex
```

Installs `git` + `uv` if missing, clones the repo into
`%LOCALAPPDATA%\Programs\transcription`, syncs ~3 GB of CUDA wheels, creates
Desktop + Start Menu shortcuts. Set the HuggingFace token (for diarization)
from the first-run wizard, or via `uv run transcription config set-token
huggingface`. Full reference in [`packaging/README.md`](packaging/README.md).

</details>

## Install on macOS (~10 min)

1. Download [`packaging/install.sh`](packaging/install.sh) — click **Raw**, save as.
2. Run the installer:
   ```bash
   bash install.sh
   ```
   Installs `git` + `uv` if missing, clones the repo into `~/Applications/transcription`, syncs ~3 GB of CPU/MPS wheels, creates `~/.local/bin/transcription-gui`.
3. Set your HuggingFace token:
   ```bash
   cd ~/Applications/transcription
   uv run transcription config set-token huggingface
   ```
4. For system-audio capture, install [BlackHole](https://existential.audio/blackhole/): `brew install --cask blackhole-2ch`
5. Launch: `transcription-gui`

## Usage

### GUI (primary)

```bash
transcription gui
```

Single native window, three cards:
- **Recording** — Start / Stop buttons + live chrono. Stop auto-enqueues a transcription job.
- **Jobs** — live table of recent jobs; click a `done` row to open the folder, click a `failed` row for the error.
- **Recordings** — 10 most recent + an Open button.

The GUI runs its own worker thread inside the same process — no separate daemon needed.

### CLI (alternative / power user)

```bash
transcription record --duration 60       # records, auto-enqueues
transcription daemon                      # runs the background worker (CLI-only flow)
transcription jobs list                   # show the queue
transcription transcribe <id>             # synchronous one-off (blocks until done)
transcription doctor                      # health check (Python, CUDA/MPS, HF token, jobs DB)
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
   Audio capture                   SQLite job queue
   mic + loopback                  (jobs.db, WAL mode)
   (WASAPI on Win / CoreAudio+BlackHole on mac)
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
                            ├── WhisperXLocalBackend     (GPU or CPU)
                            │     + faster-whisper (CPU path, auto)
                            └── RemoteOpenAICompatBackend  (OpenAI, Groq,
                                                            self-hosted)
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

- **SQLite is the only IPC** between the GUI and the worker. No sockets, no PID files. `sqlite3 <config-dir>/transcription/jobs.db` is the debugging tool (config dir: `%APPDATA%\transcription` on Windows, `~/Library/Application Support/transcription` on macOS).
- **Speaker labels are namespaced at merge time**: the mic's `SPEAKER_00` and the system's `SPEAKER_00` are different people, so we relabel them globally (`MIC`, `SYSTEM_S0`, `SYSTEM_S1`) in `transcript.md`. Per-track JSON files keep the raw pyannote labels.
- **Backend selection goes through a single switch** (`backends/factory.py::get_backend`). Two implementations ship today: `WhisperXLocalBackend` (GPU/CPU) and `RemoteOpenAICompatBackend` (any provider exposing the OpenAI `/audio/transcriptions` endpoint). Adding another remote provider with a non-OpenAI shape is one new file plus one branch in the factory.
- **Packaging uses `uv` over PyInstaller.** The install is a managed git checkout that updates with `git pull`; no 3-4 GB single binary to rebuild for every torch / whisperx version bump. See [`packaging/README.md`](packaging/README.md) for the rationale.
- **Crashed recordings are auto-recovered.** If the PC shuts down mid-record, `transcription recover` (or the next GUI launch) finalizes the audio files into a proper recording + enqueues the job.

## Configuration

Lives in the platform config dir: `%APPDATA%\transcription\config.toml` on Windows, `~/Library/Application Support/transcription/config.toml` on macOS. Tokens (HuggingFace, remote API keys) live in the OS keychain (Windows Credential Manager / macOS Keychain) via `transcription config set-token <service>` — never in a file.

Most-used keys:

| Key                            | Default        | What it controls                                                                 |
|--------------------------------|----------------|----------------------------------------------------------------------------------|
| `backend_mode`                 | (wizard)       | `local_gpu` / `local_cpu` / `remote_api`. Set by the first-run wizard.           |
| `model`                        | `small`        | Whisper model size for the local backend (`tiny` → `large-v3`).                  |
| `language`                     | `null`         | ISO code (`fr`, `en`, …) or `null` to auto-detect.                               |
| `recording_format`             | `flac`         | `flac` (recommended, lossless, ~½ of WAV) or `wav`.                              |
| `recording_sample_rate`        | `16000`        | Hz. Whisper / pyannote both resample to 16 kHz internally — higher just wastes disk. |
| `remote_api_base_url`          | `null`         | Used when `backend_mode=remote_api`. E.g. `https://api.groq.com/openai/v1`.      |
| `remote_api_model`             | `null`         | Model name as the remote provider expects it (e.g. `whisper-large-v3`).          |
| `remote_api_token_service`     | `remote_api`   | Keyring slot name to read the API key from. Override if you keep several providers' keys side by side. |
| `remote_api_timeout_seconds`   | `600`          | HTTP timeout per request. Long enough for a 10+ min clip on a slow link.         |

Per-recording overrides via `transcription record --model medium --sample-rate 48000 --format wav`.

### Remote API backend (no local CUDA install needed)

If you don't have a GPU and don't want to spend ~30-60 min/h transcribing on CPU,
point the app at any OpenAI-compatible `/audio/transcriptions` endpoint. Tested
shapes:

| Provider          | `remote_api_base_url`                | `remote_api_model`        | Free tier                         |
|-------------------|--------------------------------------|---------------------------|-----------------------------------|
| Groq              | `https://api.groq.com/openai/v1`     | `whisper-large-v3`        | Generous free tier, no CB needed  |
| OpenAI            | `https://api.openai.com/v1`          | `whisper-1`               | Pay-as-you-go (~$0.006/min)       |
| Self-hosted       | e.g. `http://localhost:8080/v1`      | depends on the server     | Free                              |

Setup (example with Groq):

```bash
transcription config set backend_mode remote_api
transcription config set remote_api_base_url https://api.groq.com/openai/v1
transcription config set remote_api_model whisper-large-v3
transcription config set-token remote_api          # paste API key when prompted
transcription doctor                                # verifies the whole chain
```

Trade-off: the OpenAI API has no diarization, so the **system track collapses
to a single speaker**. The mic track was already SOLO by convention, so only
multi-person system audio (Discord call, Teams meeting) loses speaker
separation. Run `local_gpu` / `local_cpu` if you need that.

## Tech stack

| | |
|---|---|
| Audio capture        | [soundcard](https://github.com/bastibe/SoundCard) (WASAPI on Windows, CoreAudio on macOS) |
| Transcription        | [WhisperX](https://github.com/m-bain/whisperX) (Whisper + word-level alignment) |
| Speaker diarization  | [pyannote-audio](https://github.com/pyannote/pyannote-audio) (`speaker-diarization-community-1`) |
| CLI                  | [Typer](https://typer.tiangolo.com/) |
| Desktop UI           | [NiceGUI](https://nicegui.io/) + [pywebview](https://pywebview.flowrl.com/) (Edge WebView2 on Windows, WebKit on macOS) |
| Env / packaging      | [uv](https://docs.astral.sh/uv/) |
| Job queue            | SQLite (stdlib, WAL mode) |
| Token storage        | [keyring](https://github.com/jaraco/keyring) (Windows Credential Manager / macOS Keychain) |

## Limitations and what's NOT done

- **System audio loopback on macOS requires BlackHole + a Multi-Output Device.** Unlike Windows (WASAPI loopback is built-in), macOS needs a virtual audio device to capture speaker output. [BlackHole](https://existential.audio/blackhole/) is the recommended free option (`brew install --cask blackhole-2ch`). You then need to create a *Multi-Output Device* in Audio MIDI Setup that routes audio to both your speakers and BlackHole (see [`packaging/README.md`](packaging/README.md)). Microphone recording works without any of this.
- **System audio is captured as one combined stream.** No per-app separation yet (Discord-only / Teams-only / browser-only). The Windows Process Loopback API would unlock this on Windows — planned for v2.
- **Remote backend has no diarization.** `RemoteOpenAICompatBackend` falls back to single-speaker labelling — the OpenAI `/audio/transcriptions` surface doesn't expose speaker turns. For multi-speaker system audio (Discord calls, Teams meetings) keep `local_gpu` / `local_cpu`, or wait for a future provider-specific backend (Deepgram / AssemblyAI both diarize).
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
