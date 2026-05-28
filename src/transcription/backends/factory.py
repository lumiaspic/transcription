"""Backend factory.

Picks the right TranscriptionBackend implementation based on the user's
`backend_mode` config choice (set by the first-run wizard) or assembles a
fallback chain from `backend_fallback_chain`.

Two implementations exist:
- `WhisperXLocalBackend` for both `local_gpu` and `local_cpu` (it picks the
  device at runtime; the mode distinction matters only for UX).
- `RemoteOpenAICompatBackend` for any remote OpenAI-compatible
  `/audio/transcriptions` endpoint. The same class powers multiple chain
  entries — each one resolves its own `base_url` / `model` / `token_service`
  via `config.resolve_remote_backend_config(name, …)`.
"""

from __future__ import annotations

from ..config import load_config
from .base import TranscriptionBackend

# Built-in local modes. Remote entries are user-named (the legacy
# "remote_api" identifier is one such name, kept here for ergonomics).
LOCAL_MODES = ("local_gpu", "local_cpu")
KNOWN_MODES = (*LOCAL_MODES, "remote_api")


def _construct(name: str, model: str | None = None) -> TranscriptionBackend:
    """Build a single backend by identifier. Local modes match a built-in
    class; anything else is treated as a named remote OpenAI-compatible
    endpoint."""
    if name in LOCAL_MODES:
        from .whisperx_local import WhisperXLocalBackend

        return WhisperXLocalBackend(model=model)

    from .remote_openai_compat import RemoteOpenAICompatBackend

    return RemoteOpenAICompatBackend(model=model, backend_name=name)


def get_backend(model: str | None = None, mode: str | None = None) -> TranscriptionBackend:
    """Construct the single backend the user has chosen.

    `mode` overrides the config (useful for the CLI to force a specific
    backend without touching the persisted choice). If neither is set, falls
    back to 'local_gpu'.

    For chains of backends with fallback, use `get_backend_chain()` instead.
    """
    if mode is None:
        mode = load_config().get("backend_mode") or "local_gpu"

    if mode in LOCAL_MODES or mode == "remote_api":
        return _construct(mode, model=model)

    raise ValueError(f"Unknown backend_mode={mode!r}. Known: {KNOWN_MODES}")


def get_backend_chain(model: str | None = None) -> list[TranscriptionBackend]:
    """Return the full fallback chain as a list of constructed backends.

    Defaults to a single-entry chain == [backend_mode] when
    `backend_fallback_chain` is unset, which preserves the pre-chain behaviour
    of `get_backend()`.

    Each entry is constructed eagerly so config errors surface up front
    instead of mid-recording.
    """
    cfg = load_config()
    chain = cfg.get("backend_fallback_chain") or [cfg.get("backend_mode") or "local_gpu"]
    if not isinstance(chain, list) or not chain:
        raise ValueError(f"backend_fallback_chain must be a non-empty list, got {chain!r}")
    return [_construct(name, model=model) for name in chain]
