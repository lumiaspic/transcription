<#
.SYNOPSIS
    Remove shortcuts and the install directory.

.DESCRIPTION
    Does NOT delete user data:
      - Config + tokens : %APPDATA%\transcription\  and the Windows
                          Credential Manager entry for the HF token.
      - Recordings      : wherever you've kept them (default location is
                          inside the install dir, but most users move
                          them or set a custom recordings_dir in config).

    Re-running install.ps1 at any time will pick up the preserved
    config and tokens.

.PARAMETER InstallDir
    Install directory to remove.

.PARAMETER Yes
    Skip confirmation prompts.
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "$env:LOCALAPPDATA\Programs\transcription",
    [switch]$Yes
)

$ErrorActionPreference = "Stop"

$targets = @(
    "$([Environment]::GetFolderPath('Desktop'))\Transcription.lnk",
    "$([Environment]::GetFolderPath('Programs'))\Transcription.lnk"
)

$any = $false

foreach ($t in $targets) {
    if (Test-Path $t) {
        $any = $true
        if ($Yes -or (Read-Host "Remove shortcut $t ? [y/N]") -eq "y") {
            Remove-Item $t -Force
            Write-Host "Removed $t" -ForegroundColor Green
        }
    }
}

if (Test-Path $InstallDir) {
    $any = $true
    if ($Yes -or (Read-Host "Remove install dir $InstallDir ? [y/N]") -eq "y") {
        Remove-Item -Recurse -Force $InstallDir
        Write-Host "Removed $InstallDir" -ForegroundColor Green
    }
}

if (-not $any) {
    Write-Host "Nothing to remove."
    exit 0
}

Write-Host ""
Write-Host "Note: your config and tokens (in %APPDATA%\transcription and Windows" -ForegroundColor Yellow
Write-Host "Credential Manager) were left intact. Re-running install.ps1 will" -ForegroundColor Yellow
Write-Host "reuse them automatically." -ForegroundColor Yellow
