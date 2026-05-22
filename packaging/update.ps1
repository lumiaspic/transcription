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

if (-not (Test-Path "$InstallDir\.git")) {
    throw "No git checkout at $InstallDir. Run install.ps1 first."
}

Write-Host "==> Updating $InstallDir" -ForegroundColor Cyan
git -C $InstallDir fetch --quiet origin $Branch
$before = git -C $InstallDir rev-parse --short HEAD
git -C $InstallDir checkout --quiet $Branch
git -C $InstallDir reset --hard --quiet "origin/$Branch"
$after = git -C $InstallDir rev-parse --short HEAD

if ($before -eq $after) {
    Write-Host "    Already up to date at $after" -ForegroundColor Green
    exit 0
}

Write-Host "    $before -> $after" -ForegroundColor Green
Write-Host "==> Re-syncing dependencies" -ForegroundColor Cyan
Push-Location $InstallDir
try {
    uv sync --extra transcribe
} finally {
    Pop-Location
}
Write-Host "    Done." -ForegroundColor Green
