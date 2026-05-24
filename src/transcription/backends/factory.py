"""Backend factory.

Picks the right TranscriptionBackend implementation based on the user's
`backend_mode` config choice (set by the first-run wizard).

For now there is only one real implementation (WhisperXLocalBackend) used
for both local_gpu and local_cpu — the hardware detection inside
WhisperXLocalBackend._device() already adapts. The two modes are distinct
only for UX (the wizard message + later RemoteAPI swap-in).
"""

from __future__ import annotations

from ..config import load_config
from .base import TranscriptionBackend
from .whisperx_local import WhisperXLocalBackend

KNOWN_MODES = ("local_gpu", "local_cpu", "remote_api")


class RemoteBackendNotImplemented(NotImplementedError):
    """Raised when backend_mode='remote_api' but no remote impl is available yet."""


def get_backend(model: str | None = None, mode: str | None = None) -> TranscriptionBackend:
    """Construct the backend instance the user has chosen.

    `mode` overrides the config (useful for the CLI to force a specific
    backend without touching the persisted choice). If neither is set, falls
    back to 'local_gpu' which is what WhisperXLocalBackend handles natively.
    """
    if mode is None:
        mode = load_config().get("backend_mode") or "local_gpu"

    if mode in ("local_gpu", "local_cpu"):
        # Same class for both — WhisperXLocalBackend picks the device at runtime.
        # The mode distinction matters only for UX surfaces (wizard, doctor).
        return WhisperXLocalBackend(model=model)

    if mode == "remote_api":
        raise RemoteBackendNotImplemented(
            "Remote API backend is not implemented yet. "
            "Run `transcription config set backend_mode local_gpu` "
            "(or local_cpu) to switch back to local."
        )

    raise ValueError(f"Unknown backend_mode={mode!r}. Known: {KNOWN_MODES}")
