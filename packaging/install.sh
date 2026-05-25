#!/usr/bin/env bash
# Transcription — one-shot installer for macOS.
#
# Installs uv + git if missing, clones (or updates) the repository,
# provisions the Python environment with all transcribe extras, and
# creates a launcher script at ~/Applications/transcription.
#
# Re-runnable: existing installs are updated in place.
#
# Usage:
#   bash install.sh
#   bash install.sh --install-dir ~/apps/transcription
#   bash install.sh --branch main --no-launch

set -euo pipefail

# ---------- defaults ----------

INSTALL_DIR="${HOME}/Applications/transcription"
BRANCH="main"
REPO_URL="https://github.com/lumiaspic/transcription.git"
NO_LAUNCH=0

# ---------- argument parsing ----------

while [[ $# -gt 0 ]]; do
    case "$1" in
        --install-dir)  INSTALL_DIR="$2"; shift 2 ;;
        --branch)       BRANCH="$2";      shift 2 ;;
        --repo-url)     REPO_URL="$2";    shift 2 ;;
        --no-launch)    NO_LAUNCH=1;      shift   ;;
        *) echo "Unknown option: $1" >&2; exit 1  ;;
    esac
done

# ---------- helpers ----------

step() { echo ""; echo "==> $1"; }
done_() { echo "    $1"; }
note() { echo "    [note] $1"; }

step "Transcription installer (macOS)"
echo "    Target : $INSTALL_DIR"
echo "    Branch : $BRANCH"
echo "    Repo   : $REPO_URL"

# ---------- prerequisites ----------

step "Checking prerequisites"

if ! command -v git &>/dev/null; then
    note "git not found. Installing Xcode Command Line Tools..."
    xcode-select --install 2>/dev/null || true
    echo "    Re-run this script once the Xcode CLT installation completes."
    exit 1
fi
done_ "git $(git --version | sed 's/git version //')"

if ! command -v uv &>/dev/null; then
    note "uv not found, installing via Astral's installer..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="${HOME}/.local/bin:${PATH}"
    if ! command -v uv &>/dev/null; then
        echo "uv install failed. See https://docs.astral.sh/uv/" >&2
        exit 1
    fi
fi
done_ "uv $(uv --version | awk '{print $2}')"

# Free disk space check. PyTorch + deps download is ~3 GB; warn early.
FREE_GB=$(df -g "${HOME}" | awk 'NR==2{print $4}')
if [[ "${FREE_GB}" -lt 6 ]]; then
    note "Free disk space: ${FREE_GB} GB (WARNING: need ~6 GB for PyTorch wheels)"
else
    done_ "Free disk space: ${FREE_GB} GB"
fi

# BlackHole is the recommended virtual audio device for system-audio loopback
# capture on macOS. Without it, only microphone recording is available.
if system_profiler SPAudioDataType 2>/dev/null | grep -qi "blackhole"; then
    done_ "BlackHole virtual audio device: found"
    note "Reminder: to actually capture system audio, create a Multi-Output Device"
    note "  in Audio MIDI Setup (NOT an Aggregate Device) combining your speakers"
    note "  and BlackHole, then select it as your system output."
else
    note "BlackHole not found — system-audio loopback capture will not work."
    note "Install it with: brew install --cask blackhole-2ch"
    note "  or download from https://existential.audio/blackhole/"
    note "  (microphone-only recording still works without BlackHole)"
fi

# ffmpeg is an optional dependency: pyannote/torchcodec emit a noisy warning at
# startup when ffmpeg's dylibs (libavutil etc.) are missing. We fall back to
# soundfile for FLAC decoding so transcription works either way, but installing
# ffmpeg suppresses the warning and unlocks decoding of other audio formats.
if command -v ffmpeg &>/dev/null; then
    done_ "ffmpeg: $(ffmpeg -version | head -1 | awk '{print $3}')"
else
    note "ffmpeg not found (optional). Silences the torchcodec warning at startup."
    note "  Install with: brew install ffmpeg"
fi

# ---------- clone or update ----------

step "Cloning or updating $REPO_URL"

if [[ -d "${INSTALL_DIR}/.git" ]]; then
    done_ "Existing checkout at ${INSTALL_DIR}, fetching ${BRANCH}..."
    git -C "${INSTALL_DIR}" fetch --quiet origin "${BRANCH}"
    git -C "${INSTALL_DIR}" checkout --quiet "${BRANCH}"
    git -C "${INSTALL_DIR}" reset --hard --quiet "origin/${BRANCH}"
    done_ "Now at $(git -C "${INSTALL_DIR}" rev-parse --short HEAD)"
else
    if [[ -d "${INSTALL_DIR}" ]] && [[ -n "$(ls -A "${INSTALL_DIR}")" ]]; then
        echo "${INSTALL_DIR} exists and is not empty." >&2
        echo "Remove it manually or pass --install-dir <other-path>." >&2
        exit 1
    fi
    mkdir -p "$(dirname "${INSTALL_DIR}")"
    note "First time on this machine. If the repo is private, git will prompt for GitHub auth."
    git clone --branch "${BRANCH}" "${REPO_URL}" "${INSTALL_DIR}"
    done_ "Cloned at $(git -C "${INSTALL_DIR}" rev-parse --short HEAD)"
fi

# ---------- sync deps ----------

step "Installing Python environment (5-10 min the first time, ~3 GB of wheels)"
cd "${INSTALL_DIR}"
if ! uv sync --extra transcribe; then
    echo "" >&2
    echo "uv sync failed. Common causes:" >&2
    echo "  - Disk full — PyTorch needs ~6 GB free during install" >&2
    echo "  - Network interrupted — re-run install.sh, uv resumes partial downloads" >&2
    exit 1
fi
done_ "Dependencies in ${INSTALL_DIR}/.venv"

# ---------- launcher script ----------

step "Creating launcher script"

LAUNCHER_DIR="${HOME}/.local/bin"
LAUNCHER="${LAUNCHER_DIR}/transcription-gui"
mkdir -p "${LAUNCHER_DIR}"

cat > "${LAUNCHER}" <<EOF
#!/usr/bin/env bash
cd "${INSTALL_DIR}"
exec uv run --extra transcribe transcription gui "\$@"
EOF
chmod +x "${LAUNCHER}"
done_ "${LAUNCHER}"

# Add ~/.local/bin to PATH hint if not already there.
if ! echo "${PATH}" | grep -q "${LAUNCHER_DIR}"; then
    note "Add the following to your shell profile (e.g. ~/.zshrc) to use the launcher from anywhere:"
    note "    export PATH=\"\${HOME}/.local/bin:\${PATH}\""
fi

# ---------- next steps ----------

step "Done"
echo ""
echo "Next steps:"
echo ""
echo "  1. (Once per machine) Set your HuggingFace token to enable"
echo "     speaker diarization on the system track:"
echo "         cd \"${INSTALL_DIR}\""
echo "         uv run transcription config set-token huggingface"
echo ""
echo "     You also need to accept the licence at:"
echo "     https://huggingface.co/pyannote/speaker-diarization-community-1"
echo ""
echo "  2. Launch the GUI:"
echo "         ${LAUNCHER}"
echo "     or:"
echo "         cd \"${INSTALL_DIR}\" && uv run --extra transcribe transcription gui"
echo ""
echo "  Update later  : bash ${INSTALL_DIR}/packaging/install.sh"
echo "  Health check  : uv run transcription doctor"
echo ""

if [[ "${NO_LAUNCH}" -eq 0 ]]; then
    step "Launching Transcription GUI..."
    cd "${INSTALL_DIR}"
    uv run --extra transcribe transcription gui &
fi
