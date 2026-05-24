"""Tests for `backends.factory` — pick the right TranscriptionBackend.

The factory itself is a simple switch on `backend_mode`. We can test all
branches without triggering torch/whisperx imports because
`WhisperXLocalBackend.__init__` is lightweight (no model load) — only its
`.transcribe()` method actually pulls torch.
"""

from __future__ import annotations

import pytest

from transcription.backends.factory import (
    KNOWN_MODES,
    RemoteBackendNotImplemented,
    get_backend,
)
from transcription.backends.whisperx_local import WhisperXLocalBackend


class TestGetBackend:
    @pytest.mark.parametrize("mode", ["local_gpu", "local_cpu"])
    def test_local_modes_construct_whisperx_local_backend(
        self,
        isolated_config_dir,  # noqa: ARG002 — isolation for load_config() inside __init__
        mode: str,
    ) -> None:
        backend = get_backend(mode=mode)

        assert isinstance(backend, WhisperXLocalBackend)

    def test_remote_api_mode_raises_explicit_notimplemented(
        self,
        isolated_config_dir,  # noqa: ARG002
    ) -> None:
        # Better than silently falling back: tells the user how to fix their config.
        with pytest.raises(RemoteBackendNotImplemented, match="config set backend_mode"):
            get_backend(mode="remote_api")

    def test_unknown_mode_raises_value_error_listing_known_modes(
        self,
        isolated_config_dir,  # noqa: ARG002
    ) -> None:
        with pytest.raises(ValueError) as exc_info:
            get_backend(mode="quantum_supremacy")

        # The error message points to the valid alternatives, not just a code.
        msg = str(exc_info.value)
        assert "quantum_supremacy" in msg
        for known in KNOWN_MODES:
            assert known in msg

    def test_mode_arg_overrides_config(
        self,
        isolated_config_dir,  # noqa: ARG002
    ) -> None:
        # Persist a config with mode=remote_api, but pass mode=local_cpu explicitly.
        from transcription import config as cfg

        cfg.save_config({**cfg.DEFAULT_CONFIG, "backend_mode": "remote_api"})

        backend = get_backend(mode="local_cpu")  # should NOT raise

        assert isinstance(backend, WhisperXLocalBackend)

    def test_defaults_to_local_gpu_when_no_config_or_arg(
        self,
        isolated_config_dir,  # noqa: ARG002
    ) -> None:
        # Fresh config dir, no backend_mode key → fallback documented as local_gpu.
        backend = get_backend()

        assert isinstance(backend, WhisperXLocalBackend)

    def test_reads_backend_mode_from_config_when_no_arg(
        self,
        isolated_config_dir,  # noqa: ARG002
    ) -> None:
        from transcription import config as cfg

        cfg.save_config({**cfg.DEFAULT_CONFIG, "backend_mode": "remote_api"})

        with pytest.raises(RemoteBackendNotImplemented):
            get_backend()  # picks up remote_api from disk

    def test_model_arg_is_passed_through_to_backend(
        self,
        isolated_config_dir,  # noqa: ARG002
    ) -> None:
        backend = get_backend(model="large-v3", mode="local_cpu")

        assert backend.model_name == "large-v3"
