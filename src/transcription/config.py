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
#
# `remote_api` is the generic slot used by the OpenAI-compatible backend
# (RemoteOpenAICompatBackend). It covers OpenAI, Groq, self-hosted whisper.cpp
# / faster-whisper-server, and anything else exposing
# `POST /audio/transcriptions`. Users who juggle several providers can pick a
# different keyring service via the `remote_api_token_service` config key
# (e.g. set it to "groq" and store the token under that name).
KNOWN_TOKEN_SERVICES = {
    "huggingface": "HuggingFace token (for pyannote diarization models)",
    "remote_api": "Remote transcription API key (OpenAI / Groq / self-hosted)",
}


# ---------- Plain config (TOML) ----------

DEFAULT_CONFIG: dict[str, Any] = {
    "backend": "whisperx_local",
    "backend_mode": None,  # None = unset; set by first-run wizard. local_gpu|local_cpu|remote_api
    "model": "small",
    "language": None,  # None = auto-detect
    # UI display language. None = auto-detect from the OS locale (falls back to
    # English). Set to a supported code ("en", "fr") to force it. See i18n.py.
    "ui_language": None,
    "compute_type_cuda": "float16",
    "compute_type_cpu": "int8",
    "recordings_dir": None,  # None = ./recordings in cwd
    # Audio recording knobs. Whisper AND pyannote both resample to 16 kHz
    # internally, so 16 kHz is optimal for the transcription use case
    # (~6× smaller than 48 kHz with zero accuracy impact). Bump to 48000 if
    # you also want playback-quality archives.
    "recording_sample_rate": 16000,
    # Storage format. Opus (in an Ogg container) is lossy but transparent for
    # speech and ~10× smaller than FLAC, which keeps recordings small and well
    # under remote-API upload caps. FLAC stays available for a lossless
    # archive; WAV for raw uncompressed PCM.
    "recording_format": "opus",  # "opus" (compressed) | "flac" (lossless) | "wav"
    # Remote API backend (used only when backend_mode == "remote_api").
    # Any OpenAI-compatible /audio/transcriptions endpoint works:
    #   - OpenAI       : https://api.openai.com/v1
    #   - Groq         : https://api.groq.com/openai/v1
    #   - whisper.cpp  : http://localhost:8080/v1   (self-hosted)
    # `remote_api_token_service` is the name under which the API key is stored
    # in the OS keyring — defaults to "remote_api" but can point at a custom
    # slot if you keep multiple providers' keys side by side.
    "remote_api_base_url": None,
    "remote_api_model": None,
    "remote_api_token_service": "remote_api",
    "remote_api_timeout_seconds": 600,
    # Upload pre-compression. Set to a number of MB above which the remote
    # backend re-encodes the source to mono Opus before upload. 20 MB leaves
    # headroom under the common 25 MB provider cap. Set to a very large
    # number to disable.
    "remote_api_max_upload_mb": 20,
    "remote_api_compress_codec": "opus",
    "remote_api_compress_bitrate": "16k",
    # Fallback chain. List of backend identifiers tried in order: on a
    # recoverable error (quota / rate-limit / payload-too-large) the next
    # entry is tried for the *current track only*. Entries can be the
    # built-in modes ("local_gpu", "local_cpu", "remote_api") or a custom
    # name pointing at a [backends.NAME] table — see resolve_remote_backend_config.
    # When unset, falls back to [backend_mode] (single-entry chain == old behavior).
    "backend_fallback_chain": None,
    # Optional nested config for named remote backends:
    #   [backends.openai_fallback]
    #   base_url = "https://api.openai.com/v1"
    #   model = "whisper-1"
    #   token_service = "openai_api"
    "backends": {},
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


def resolve_remote_backend_config(name: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """Look up a remote backend's settings by chain identifier.

    Two layouts coexist for backwards compatibility:
      - Flat keys `remote_api_*` (the original single-backend layout).
        Used when `name == "remote_api"` and no nested entry shadows them.
      - Nested `[backends.NAME]` table with `base_url` / `model` /
        `token_service` / `timeout_seconds`. The nested entry wins when both
        are present, which lets a user gradually migrate.

    For a custom name like "openai_fallback", `token_service` defaults to the
    name itself so each provider can keep its own key in the OS keyring
    without extra configuration.
    """
    backends = cfg.get("backends") or {}
    entry = backends.get(name) or {}
    if name == "remote_api":
        return {
            "base_url": entry.get("base_url") or cfg.get("remote_api_base_url"),
            "model": entry.get("model") or cfg.get("remote_api_model"),
            "token_service": entry.get("token_service")
            or cfg.get("remote_api_token_service")
            or "remote_api",
            "timeout_seconds": entry.get("timeout_seconds")
            or cfg.get("remote_api_timeout_seconds")
            or 600,
        }
    return {
        "base_url": entry.get("base_url"),
        "model": entry.get("model"),
        "token_service": entry.get("token_service") or name,
        "timeout_seconds": entry.get("timeout_seconds") or 600,
    }


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
