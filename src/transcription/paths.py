"""Standard file system paths for the app."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "transcription"


def config_dir() -> Path:
    """Per-user config dir.

    - Windows : %APPDATA%\\transcription\\
    - macOS   : ~/Library/Application Support/transcription/
    - Linux   : ~/.config/transcription/
    """
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    p = Path(base) / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_file() -> Path:
    return config_dir() / "config.toml"


def recordings_dir() -> Path:
    """Where audio + transcripts live. Defaults to ./recordings/ in the cwd.

    Overridable via config.toml (key: recordings_dir).
    """
    from .config import load_config  # late import to avoid cycle

    cfg = load_config()
    override = cfg.get("recordings_dir")
    p = Path(override) if override else Path.cwd() / "recordings"
    p.mkdir(parents=True, exist_ok=True)
    return p


def logs_dir() -> Path:
    p = config_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def jobs_db() -> Path:
    """Persistent SQLite queue file."""
    return config_dir() / "jobs.db"
