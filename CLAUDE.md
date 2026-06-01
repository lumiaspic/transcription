# CLAUDE.md

Guidance for AI coding assistants working in this repository.

## Project overview

Local Windows desktop app that captures mic + system audio as two separate compressed tracks (Opus by default; FLAC/WAV configurable), then auto-transcribes them with WhisperX and pyannote speaker diarization. Produces per-track `.txt` / `.srt` / `.json` plus a merged chronological Markdown transcript.

Key design choices:
- **SQLite is the only IPC** between GUI and worker (no sockets, no PID files).
- **Two backend implementations** behind one `TranscriptionBackend` interface: `WhisperXLocalBackend` (GPU/CPU) and `RemoteOpenAICompatBackend` (any OpenAI-compatible `/audio/transcriptions` endpoint — OpenAI, Groq, self-hosted whisper.cpp, …). The remote backend has no diarization, so MULTI tracks fall back to single-speaker.
- **Packaging via `uv`**: managed git checkout updated with `git pull`, no single-binary build.
- **Speaker labels are namespaced at merge time**: `MIC`, `SYSTEM_S0`, `SYSTEM_S1`.

## Repository layout

```
src/transcription/
  audio/          # WASAPI audio capture (soundcard, soundfile)
  backends/       # TranscriptionBackend interface + WhisperX implementation
  pipeline/       # Worker, job queue, health check, recovery, speaker profiles
  ui/             # NiceGUI desktop UI (excluded from CI test coverage)
  cli.py          # Typer CLI entry point
  config.py       # TOML config (~/.appdata/transcription/config.toml)
  paths.py        # Platform paths
tests/            # pytest suite; mocks hardware/UI stack
packaging/        # install.ps1 + packaging README
```

## Development commands

```powershell
uv sync --group dev          # install dev deps (no transcribe stack)
uv run pytest                # run tests
uv run ruff check .          # lint
uv run ruff format --check . # format check
uv run transcription --help  # CLI
```

## Conventions

- **Language**: English everywhere — code, comments, commit messages, PR descriptions.
- **No French**: the codebase was initially partly in French; any remaining French should be translated.
- **Comments**: only when the *why* is non-obvious. No summary docstrings.
- **Tests**: mock at the boundary of hardware/UI-coupled code. The `transcribe` extra (~3 GB) is not installed in CI.
- **Config**: lives in `%APPDATA%\transcription\config.toml`. Secrets (HuggingFace token, remote API key) live in Windows Credential Manager via `keyring`.

## CI

Two jobs in `.github/workflows/ci.yml`:
- `lint`: runs Ruff on ubuntu-latest.
- `test`: runs pytest on windows-latest for Python 3.11 and 3.12; uploads coverage to Codecov.

Dependabot runs weekly for both Python deps (uv) and GitHub Actions.
