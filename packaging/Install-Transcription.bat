@echo off
title Transcription - Installer
echo.
echo  ============================================
echo    Transcription - one-click installer
echo  ============================================
echo.
echo  This downloads and runs the installer. It will:
echo    - install git and uv if they are missing
echo    - download the app (~3 GB the first time)
echo    - put a "Transcription" shortcut on your Desktop
echo.
echo  Keep this window open until it says "Done".
echo.
pause

powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/lumiaspic/transcription/main/packaging/install.ps1 | iex"

echo.
if errorlevel 1 (
  echo  Something went wrong. Scroll up to read the error in red,
  echo  then just run this file again - it resumes where it stopped.
) else (
  echo  All set. Launch "Transcription" from your Desktop.
)
echo.
pause
