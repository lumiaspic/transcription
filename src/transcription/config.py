"""User config + secret token storage.

- Plain config lives in %APPDATA%\\transcription\\config.toml (human-editable).
- Secrets (HF token, API keys) live in the OS keyring (Windows Credential Manager).
  We NEVER write tokens to disk in plain text.
"""
from __future__ import annotations

import sys
import tomllib
from typing import Any

import keyring
import tomli_w

from .paths import config_file


KEYRING_SERVICE = "transcription-app"

# Known token services. Adding a new remote backend means adding a name here.
KNOWN_TOKEN_SERVICES = {
    "huggingface": "HuggingFace token (for pyannote diarization models)",
    "replicate": "Replicate API token (future remote backend)",
    "runpod": "RunPod API key (future remote backend)",
}


# ---------- Plain config (TOML) ----------

DEFAULT_CONFIG: dict[str, Any] = {
    "backend": "whisperx_local",
    "backend_mode": None,  # None = unset; set by first-run wizard. local_gpu|local_cpu|remote_api
    "model": "small",
    "language": None,  # None = auto-detect
    "compute_type_cuda": "float16",
    "compute_type_cpu": "int8",
    "recordings_dir": None,  # None = ./recordings in cwd
}


def load_config() -> dict[str, Any]:
    path = config_file()
    if not path.exists():
        return dict(DEFAULT_CONFIG)
    with path.open("rb") as f:
        data = tomllib.load(f)
    # merge with defaults so new keys are present
    merged = dict(DEFAULT_CONFIG)
    merged.update(data)
    return merged


def save_config(cfg: dict[str, Any]) -> None:
    path = config_file()
    # Strip None values - tomli-w can't serialize them
    serializable = {k: v for k, v in cfg.items() if v is not None}
    with path.open("wb") as f:
        tomli_w.dump(serializable, f)


def set_config_key(key: str, value: Any) -> None:
    cfg = load_config()
    cfg[key] = value
    save_config(cfg)


# ---------- Token secrets (keyring) ----------

def set_token(service: str, token: str) -> None:
    """Store a token in the OS keyring under our service namespace."""
    if not token:
        raise ValueError("Empty token")
    keyring.set_password(KEYRING_SERVICE, service, token)


def get_token(service: str) -> str | None:
    """Fetch a token. Returns None if not stored."""
    try:
        return keyring.get_password(KEYRING_SERVICE, service)
    except keyring.errors.KeyringError as e:
        print(f"[config] keyring error for {service}: {e}", file=sys.stderr)
        return None


def remove_token(service: str) -> bool:
    """Delete a token. Returns True if removed, False if it wasn't set."""
    try:
        keyring.delete_password(KEYRING_SERVICE, service)
        return True
    except keyring.errors.PasswordDeleteError:
        return False


def list_token_status() -> dict[str, bool]:
    """For each known service, whether a token is set."""
    return {svc: (get_token(svc) is not None) for svc in KNOWN_TOKEN_SERVICES}
