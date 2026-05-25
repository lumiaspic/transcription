"""Backend factory.

Picks the right TranscriptionBackend implementation based on the user's
`backend_mode` config choice (set by the first-run wizard).

Two implementations exist:
- `WhisperXLocalBackend` for both `local_gpu` and `local_cpu` (it picks the
  device at runtime; the mode distinction matters only for UX).
- `RemoteOpenAICompatBackend` for `remote_api` — talks to any
  OpenAI-compatible `/audio/transcriptions` endpoint (OpenAI, Groq,
  self-hosted whisper.cpp, …) so a single class covers every provider.
"""

from __future__ import annotations

from ..config import load_config
from .base import TranscriptionBackend

KNOWN_MODES = ("local_gpu", "local_cpu", "remote_api")


def get_backend(model: str | None = None, mode: str | None = None) -> TranscriptionBackend:
    """Construct the backend instance the user has chosen.

    `mode` overrides the config (useful for the CLI to force a specific
    backend without touching the persisted choice). If neither is set, falls
    back to 'local_gpu' which is what WhisperXLocalBackend handles natively.

    Imports are local to keep the import-time cost low: pulling
    whisperx_local triggers torch in turn, and pulling
    remote_openai_compat triggers httpx — both are deferred until actually
    needed so e.g. `transcription config show` stays snappy.
    """
    if mode is None:
        mode = load_config().get("backend_mode") or "local_gpu"

    if mode in ("local_gpu", "local_cpu"):
        from .whisperx_local import WhisperXLocalBackend

        return WhisperXLocalBackend(model=model)

    if mode == "remote_api":
        from .remote_openai_compat import RemoteOpenAICompatBackend

        return RemoteOpenAICompatBackend(model=model)

    raise ValueError(f"Unknown backend_mode={mode!r}. Known: {KNOWN_MODES}")
