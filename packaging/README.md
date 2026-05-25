# Installation

- **Windows** → [`install.ps1`](#windows) (PowerShell, Desktop shortcut)
- **macOS** → [`install.sh`](#macos) (Bash, launcher script)

---

## Windows

This isn't a `.exe` installer — it's a one-shot PowerShell script that
clones the repo, installs `uv` and `git` if missing, provisions the
Python environment with all CUDA wheels, and drops a Desktop shortcut.
It works on both a GPU-equipped gaming PC and a CPU-only work PC; the
first-run wizard picks the right backend mode.

### Install (5–10 min on a fresh machine)

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

### Update

```powershell
& "$env:LOCALAPPDATA\Programs\transcription\packaging\update.ps1"
```

This runs `git fetch` + `reset --hard origin/main` + `uv sync` — fast
when there's nothing new, otherwise picks up the latest changes.

### Uninstall

```powershell
& "$env:LOCALAPPDATA\Programs\transcription\packaging\uninstall.ps1"
```

User data (config, HF token, recordings) is preserved. Re-running
`install.ps1` will pick up where you left off.

### Custom install location

```powershell
.\install.ps1 -InstallDir C:\apps\transcription
```

### Troubleshooting

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

---

## macOS

`install.sh` is a Bash script that clones the repo, installs `uv` and
`git` if missing, provisions the Python environment, and creates a
launcher script at `~/.local/bin/transcription-gui`.

### Prerequisites

**System audio loopback** (capturing speaker output) requires a virtual
audio device. [BlackHole](https://existential.audio/blackhole/) is the
recommended free option:

```bash
brew install --cask blackhole-2ch
```

After installing BlackHole, you need to route your system audio through
it AND still hear it on your speakers. The correct macOS construct for
this is a **Multi-Output Device** (NOT an Aggregate Device — that one
combines devices as a single multi-channel interface and breaks normal
playback):

1. Open **Audio MIDI Setup** (`/System/Applications/Utilities/Audio MIDI Setup.app`)
2. Click the **+** in the lower-left → **Create Multi-Output Device**
3. Check both your real speakers (e.g. "MacBook Pro Speakers") and
   "BlackHole 2ch"
4. Set the speakers as **Primary Device** (Master Clock), and enable
   **Drift Correction** on BlackHole
5. Select this Multi-Output Device as your system output (Sound menu
   in the menu bar, or System Settings → Sound)

The app will then auto-detect BlackHole and capture its stream as the
system track. Microphone recording works without BlackHole.

> ⚠️ A known macOS limitation: when a Multi-Output Device is the system
> output, the keyboard volume keys are disabled. Adjust volume from
> Audio MIDI Setup or from the app itself (e.g. Discord, browser).

### Install (5–10 min on a fresh machine)

1. **Get `install.sh` onto the machine.** Download it from GitHub:
   - Open <https://github.com/lumiaspic/transcription/blob/main/packaging/install.sh>
     in a browser signed in to GitHub.
   - Click **Raw** → save as `install.sh`.

2. **Run the installer:**
   ```bash
   bash install.sh
   ```

   - If `git` is missing, the script prompts to install Xcode Command Line Tools.
   - If `uv` is missing, it installs it via Astral's official installer.
   - `uv sync --extra transcribe` downloads ~3 GB of PyTorch wheels
     (CPU + MPS, no CUDA). This is the slow step.

3. **Set your HuggingFace token** (one-time, enables speaker diarization):
   ```bash
   cd ~/Applications/transcription
   uv run transcription config set-token huggingface
   ```
   You also need to accept the licence on
   <https://huggingface.co/pyannote/speaker-diarization-community-1>.

4. **Launch:**
   ```bash
   transcription-gui
   ```
   or directly:
   ```bash
   cd ~/Applications/transcription
   uv run --extra transcribe transcription gui
   ```

### Update

```bash
bash ~/Applications/transcription/packaging/install.sh
```

Re-running the installer fetches the latest commits and re-syncs deps.

### Custom install location

```bash
bash install.sh --install-dir ~/apps/transcription
```

### Troubleshooting

- **GUI does not open.** Run from a terminal to see the actual error:
  ```bash
  cd ~/Applications/transcription
  uv run --extra transcribe transcription gui
  ```

- **First-run health check.**
  ```bash
  uv run transcription doctor
  ```

- **System audio not captured.** Verify BlackHole is installed and that
  your Multi-Output Device (including BlackHole) is selected as the
  system audio output. `transcription devices` should list "BlackHole 2ch"
  as a microphone — that's what the app captures.

- **`torchcodec` / `libavutil.dylib` warnings at startup.** Harmless
  (we fall back to soundfile for FLAC). Silence them with:
  ```bash
  brew install ffmpeg
  ```

- **`uv sync` fails mid-download.** Re-run the installer; uv resumes
  partial downloads automatically.
