# Installation

This isn't a `.exe` installer — it's a one-shot PowerShell script that
clones the repo, installs `uv` and `git` if missing, provisions the
Python environment with all CUDA wheels, and drops a Desktop shortcut.
It works on both a GPU-equipped gaming PC and a CPU-only work PC; the
first-run wizard picks the right backend mode.

## Install (5–10 min on a fresh machine)

1. **Get `install.ps1` onto the machine.** The repository is private,
   so the standard `irm | iex` one-liner won't work without auth — the
   easiest path is to download the script manually:
   - Open <https://github.com/lumiaspic/transcription/blob/main/packaging/install.ps1>
     in a browser that's signed in to GitHub.
   - Click **Raw** → right-click → **Save as…** → save as `install.ps1`.

2. **Open PowerShell** in the directory where you saved it.

3. **Allow script execution for this session** (Windows blocks
   unsigned downloaded scripts by default):
   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass
   ```

4. **Run the installer:**
   ```powershell
   .\install.ps1
   ```

   - If `git` is missing, it installs Git for Windows via `winget`.
   - If `uv` is missing, it installs uv via Astral's official installer.
   - On the first `git clone` of a private repo, Git Credential Manager
     pops a browser tab — sign in to GitHub once and you're done.
   - `uv sync --extra transcribe` downloads ~3 GB of PyTorch / CUDA
     wheels. This is the slow step.

5. **Set your HuggingFace token** (one-time, enables speaker
   diarization on the system track):
   ```powershell
   cd $env:LOCALAPPDATA\Programs\transcription
   uv run transcription config set-token huggingface
   ```
   You also need to accept the licence on
   <https://huggingface.co/pyannote/speaker-diarization-community-1>
   if you haven't already.

6. **Launch.** Double-click the `Transcription` shortcut on your Desktop
   (also in the Start Menu). The first-run wizard appears and asks
   which backend mode to use — pick GPU on the gaming PC, CPU on the
   work PC.

## Update

```powershell
& "$env:LOCALAPPDATA\Programs\transcription\packaging\update.ps1"
```

This runs `git fetch` + `reset --hard origin/main` + `uv sync` — fast
when there's nothing new, otherwise picks up the latest changes.

## Uninstall

```powershell
& "$env:LOCALAPPDATA\Programs\transcription\packaging\uninstall.ps1"
```

User data (config, HF token, recordings) is preserved. Re-running
`install.ps1` will pick up where you left off.

## Custom install location

```powershell
.\install.ps1 -InstallDir C:\apps\transcription
```

## Troubleshooting

- **Desktop shortcut does nothing.** Run from a terminal to see the
  actual error:
  ```powershell
  cd $env:LOCALAPPDATA\Programs\transcription
  uv run --extra transcribe transcription gui
  ```

- **First-run health check.** Always available, surfaces config issues
  in colored output:
  ```powershell
  uv run transcription doctor
  ```

- **`uv sync` fails mid-download.** Re-run the installer; uv resumes
  partial downloads automatically.

- **CUDA not detected on the gaming PC.** Check `nvidia-smi` works,
  then re-run `transcription doctor`. The error message points at
  whether the issue is the driver, PyTorch, or the cuDNN bundling.
