# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While the major version is `0`, the public surface (config format, CLI flags,
on-disk layout) may change between minor versions without warning.

## [Unreleased]

## [0.1.0] - 2026-05-25

First tagged release. Captures the project state after the public-repo cleanup
and the initial macOS port.

### Added
- WASAPI dual-track capture (mic + system audio) on Windows.
- macOS audio capture support (#11).
- WhisperX local backend with pyannote speaker diarization.
- Per-track `.txt` / `.srt` / `.json` export plus merged chronological Markdown
  transcript with namespaced speaker labels (`MIC`, `SYSTEM_S0`, ...).
- NiceGUI desktop UI with pywebview native window.
- Typer CLI entry point (`transcription`).
- SQLite-based job queue and worker with recovery on crash.
- Health check, speaker profiles, hardware probing.
- CI: Ruff lint on Linux, pytest on Windows (3.11 + 3.12), Codecov upload
  with Test Analytics.
- Pre-commit hooks (ruff + standard hygiene).
- Dependabot for `uv` and `github-actions` ecosystems, with `setuptools>=81`
  pinned out until ctranslate2 4.7+ is required.

[Unreleased]: https://github.com/lumiaspic/transcription/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/lumiaspic/transcription/releases/tag/v0.1.0
