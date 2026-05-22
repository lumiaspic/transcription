<#
.SYNOPSIS
    Transcription -- one-shot installer / updater for Windows.

.DESCRIPTION
    Installs uv + git if missing, clones (or updates) the repository,
    provisions the Python environment with all transcribe extras, and
    creates Desktop + Start Menu shortcuts that launch the GUI.

    Re-runnable: existing installs are updated in place.

.PARAMETER InstallDir
    Where to install. Default: %LOCALAPPDATA%\Programs\transcription

.PARAMETER Branch
    Git branch to track. Default: main

.PARAMETER RepoUrl
    Repository URL. Default: https://github.com/lumiaspic/transcription.git

.PARAMETER NoLaunch
    Don't auto-launch the GUI after install.

.EXAMPLE
    .\install.ps1
    Standard install to %LOCALAPPDATA%\Programs\transcription on the main branch.

.EXAMPLE
    .\install.ps1 -InstallDir C:\apps\transcription -Branch main
    Custom location.
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "$env:LOCALAPPDATA\Programs\transcription",
    [string]$Branch = "main",
    [string]$RepoUrl = "https://github.com/lumiaspic/transcription.git",
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"

function Step ($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Done ($msg) { Write-Host "    $msg" -ForegroundColor Green }
function Note ($msg) { Write-Host "    $msg" -ForegroundColor Yellow }

# PowerShell does NOT propagate native exe failures via $ErrorActionPreference --
# only its own cmdlets throw. Without this helper, a failing `uv sync` would
# silently let the script continue past it, creating shortcuts pointing at a
# broken install. Always wrap native commands with this.
function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][scriptblock]$Cmd,
        [Parameter(Mandatory = $true)][string]$Context
    )
    & $Cmd
    if ($LASTEXITCODE -ne 0) {
        throw "$Context failed (exit code $LASTEXITCODE)"
    }
}

Step "Transcription installer"
Write-Host "    Target  : $InstallDir"
Write-Host "    Branch  : $Branch"
Write-Host "    Repo    : $RepoUrl"

# ---------- prerequisites ----------

Step "Checking prerequisites"

# git (used for clone + update; Git Credential Manager handles GitHub auth)
$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) {
    Note "git not found, installing via winget..."
    winget install --id Git.Git --silent --accept-source-agreements --accept-package-agreements | Out-Null
    $env:Path = "C:\Program Files\Git\bin;$env:Path"
    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) { throw "git install failed. Install Git for Windows manually and re-run." }
}
Done "git $((git --version) -replace 'git version ','')"

# uv (Python + venv management)
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    Note "uv not found, installing via Astral's installer..."
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uv) { throw "uv install failed. See https://docs.astral.sh/uv/" }
}
Done "uv $(((uv --version) -split ' ') | Select-Object -Index 1)"

# Free disk space on the install drive. PyTorch + CUDA download is ~3 GB and
# briefly needs ~6 GB during extraction (the work PC hit "Espace insuffisant"
# at the extract step). Warn early instead of letting the user wait 5 min for
# a disk-full error.
try {
    $installDrive = (Split-Path -Qualifier $InstallDir).TrimEnd(':')
    $freeGb = (Get-PSDrive -Name $installDrive -ErrorAction Stop).Free / 1GB
    $freeStr = $freeGb.ToString('N1')
    if ($freeGb -lt 6) {
        Note "Free disk : $freeStr GB on ${installDrive}: drive  (WARNING: need ~6 GB for PyTorch+CUDA wheels)"
    } else {
        Done "Free disk : $freeStr GB on ${installDrive}: drive"
    }
} catch {
    Note "Could not read free disk space on $InstallDir : $_"
}

# ---------- clone or update ----------

Step "Cloning or updating $RepoUrl"

if (Test-Path "$InstallDir\.git") {
    Done "Existing checkout at $InstallDir, fetching $Branch..."
    Invoke-Native { git -C $InstallDir fetch --quiet origin $Branch } "git fetch"
    Invoke-Native { git -C $InstallDir checkout --quiet $Branch } "git checkout $Branch"
    Invoke-Native { git -C $InstallDir reset --hard --quiet "origin/$Branch" } "git reset --hard"
    Done "Now at $(git -C $InstallDir rev-parse --short HEAD)"
} else {
    if ((Test-Path $InstallDir) -and (@(Get-ChildItem $InstallDir -Force).Count -gt 0)) {
        throw "$InstallDir exists and is not empty. Remove it manually or pass -InstallDir <other-path>."
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $InstallDir) | Out-Null
    Note "First time on this machine. If the repo is private, a browser will open"
    Note "for GitHub authentication (Git Credential Manager)."
    Invoke-Native { git clone --branch $Branch $RepoUrl $InstallDir } "git clone"
    Done "Cloned at $(git -C $InstallDir rev-parse --short HEAD)"
}

# ---------- sync deps ----------

Step "Installing Python environment (5-10 min the first time, ~3 GB of CUDA wheels)"
Push-Location $InstallDir
try {
    try {
        Invoke-Native { uv sync --extra transcribe } "uv sync --extra transcribe"
    } catch {
        Write-Host ""
        Write-Host "uv sync failed. Common causes:" -ForegroundColor Red
        Write-Host "  - Disk full -- PyTorch+CUDA needs ~6 GB free during install" -ForegroundColor Red
        Write-Host "    (cache lives under %LOCALAPPDATA%\uv\cache by default)" -ForegroundColor Red
        Write-Host "  - Network interrupted -- re-run install.ps1, uv resumes partial downloads" -ForegroundColor Red
        Write-Host "  - Antivirus blocking writes to the uv cache or venv" -ForegroundColor Red
        try {
            $d = (Split-Path -Qualifier $InstallDir).TrimEnd(':')
            $g = ((Get-PSDrive -Name $d -ErrorAction Stop).Free / 1GB).ToString('N1')
            Write-Host ""
            Write-Host "Currently free on ${d}: drive : $g GB" -ForegroundColor Yellow
        } catch {}
        throw
    }
} finally {
    Pop-Location
}
Done "Dependencies in $InstallDir\.venv"

# ---------- shortcuts ----------

Step "Creating shortcuts"

$launcher = "$InstallDir\packaging\start.vbs"
if (-not (Test-Path $launcher)) {
    throw "Launcher missing at $launcher (repo layout changed?)"
}

$ws = New-Object -ComObject WScript.Shell
$targets = @(
    "$([Environment]::GetFolderPath('Desktop'))\Transcription.lnk",
    "$([Environment]::GetFolderPath('Programs'))\Transcription.lnk"
)
foreach ($lnkPath in $targets) {
    $shortcut = $ws.CreateShortcut($lnkPath)
    $shortcut.TargetPath = "wscript.exe"
    $shortcut.Arguments = "`"$launcher`""
    $shortcut.WorkingDirectory = $InstallDir
    # System microphone icon from shell32.dll
    $shortcut.IconLocation = "$env:WINDIR\System32\shell32.dll,138"
    $shortcut.Description = "Local audio capture + WhisperX transcription"
    $shortcut.Save()
    Done $lnkPath
}

# ---------- next steps ----------

Step "Done"
Write-Host ""
Write-Host "Next steps:"
Write-Host ""
Write-Host "  1. (Once per machine) Set your HuggingFace token to enable"
Write-Host "     speaker diarization on the system track:"
Write-Host "         cd `"$InstallDir`""
Write-Host "         uv run transcription config set-token huggingface"
Write-Host ""
Write-Host "  2. Launch via the 'Transcription' shortcut on your Desktop, or:"
Write-Host "         uv run --extra transcribe transcription gui"
Write-Host ""
Write-Host "  Update later  : packaging\update.ps1"
Write-Host "  Uninstall     : packaging\uninstall.ps1"
Write-Host "  Health check  : uv run transcription doctor"
Write-Host ""

if (-not $NoLaunch) {
    Step "Launching Transcription GUI..."
    Start-Process -FilePath "wscript.exe" -ArgumentList "`"$launcher`"" -WorkingDirectory $InstallDir
}
