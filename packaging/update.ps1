<#
.SYNOPSIS
    Pull latest changes from origin and re-sync dependencies.

    Safe to re-run; no-op if already up to date.
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "$env:LOCALAPPDATA\Programs\transcription",
    [string]$Branch = "main"
)

$ErrorActionPreference = "Stop"

# See install.ps1 for the rationale -- PowerShell doesn't propagate native exe
# failures unless we explicitly check $LASTEXITCODE.
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

if (-not (Test-Path "$InstallDir\.git")) {
    throw "No git checkout at $InstallDir. Run install.ps1 first."
}

Write-Host "==> Updating $InstallDir" -ForegroundColor Cyan
Invoke-Native { git -C $InstallDir fetch --quiet origin $Branch } "git fetch"
$before = git -C $InstallDir rev-parse --short HEAD
Invoke-Native { git -C $InstallDir checkout --quiet $Branch } "git checkout $Branch"
Invoke-Native { git -C $InstallDir reset --hard --quiet "origin/$Branch" } "git reset --hard"
$after = git -C $InstallDir rev-parse --short HEAD

if ($before -eq $after) {
    Write-Host "    Already up to date at $after" -ForegroundColor Green
    exit 0
}

Write-Host "    $before -> $after" -ForegroundColor Green
Write-Host "==> Re-syncing dependencies" -ForegroundColor Cyan
Push-Location $InstallDir
try {
    Invoke-Native { uv sync --extra transcribe } "uv sync --extra transcribe"
} finally {
    Pop-Location
}
Write-Host "    Done." -ForegroundColor Green
