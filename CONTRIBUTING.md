# Contributing

Thanks for your interest in contributing. This is a personal prototype, but bug fixes and focused improvements are welcome.

## Before you start

- For non-trivial changes, open an issue first to discuss the approach.
- Check existing issues and PRs to avoid duplicate work.

## Development setup

```powershell
git clone https://github.com/lumiaspic/transcription
cd transcription
uv sync --group dev
uv run pre-commit install
```

> The `transcribe` extra (`uv sync --extra transcribe`) pulls ~3 GB of CUDA wheels and is only needed to run the actual transcription pipeline. Tests mock this stack and run without it.

## Workflow

1. Fork the repo and create a branch from `main`.
2. Make your changes. Keep commits small and focused.
3. Ensure the test suite passes:
   ```powershell
   uv run pytest
   ```
4. Ensure linting passes:
   ```powershell
   uv run ruff check .
   uv run ruff format --check .
   ```
5. Open a pull request against `main`.

## Code conventions

- **Language**: all code, comments, commit messages, and PR descriptions must be in **English**.
- **Style**: enforced by Ruff (`ruff check` + `ruff format`). Run before pushing or let pre-commit handle it.
- **Tests**: new behaviour should come with tests. Hardware/UI-coupled code lives under `src/transcription/ui/` and `src/transcription/audio/` and is excluded from CI coverage — mock at the boundary.
- **Comments**: only when the *why* is non-obvious. No docstrings re-stating what the code already says.

## Pull request checklist

- [ ] `uv run pytest` passes locally (Windows recommended, CI will confirm)
- [ ] `uv run ruff check . && uv run ruff format --check .` passes
- [ ] New public behaviour has tests
- [ ] PR description explains *why* the change is needed, not just what it does
