"""Tests for `backends.factory` — pick the right TranscriptionBackend.

The factory itself is a simple switch on `backend_mode`. We can test all
branches without triggering torch/whisperx imports because
`WhisperXLocalBackend.__init__` is lightweight (no model load) — only its
`.transcribe()` method actually pulls torch. Same story for
`RemoteOpenAICompatBackend.__init__` which only reads config + keyring.
"""

from __future__ import annotations

import pytest

from transcription import config as cfg
from transcription.backends.factory import (
    KNOWN_MODES,
    get_backend,
)
from transcription.backends.remote_openai_compat import (
    RemoteAPIConfigError,
    RemoteOpenAICompatBackend,
)
from transcription.backends.whisperx_local import WhisperXLocalBackend


def _seed_remote_config() -> None:
    cfg.save_config(
        {
            **cfg.DEFAULT_CONFIG,
            "backend_mode": "remote_api",
            "remote_api_base_url": "https://api.example.com/v1",
            "remote_api_model": "whisper-large-v3",
        }
    )


class TestGetBackend:
    @pytest.mark.parametrize("mode", ["local_gpu", "local_cpu"])
    def test_local_modes_construct_whisperx_local_backend(
        self,
        isolated_config_dir,  # noqa: ARG002 — isolation for load_config() inside __init__
        mode: str,
    ) -> None:
        backend = get_backend(mode=mode)

        assert isinstance(backend, WhisperXLocalBackend)

    def test_remote_api_mode_constructs_remote_openai_compat_backend(
        self,
        isolated_config_dir,  # noqa: ARG002
        fake_keyring,  # noqa: ARG002 — backend doesn't need the token until .transcribe()
    ) -> None:
        # Config must be complete enough for construction to succeed — but
        # no token is required because the factory never sends a request.
        _seed_remote_config()

        backend = get_backend(mode="remote_api")

        assert isinstance(backend, RemoteOpenAICompatBackend)
        assert backend.base_url == "https://api.example.com/v1"
        assert backend.model_name == "whisper-large-v3"

    def test_remote_api_mode_with_incomplete_config_raises_config_error(
        self,
        isolated_config_dir,  # noqa: ARG002
        fake_keyring,  # noqa: ARG002
    ) -> None:
        # Fail fast at construction with an actionable message instead of
        # waiting until the worker tries to POST to a blank URL.
        cfg.save_config({**cfg.DEFAULT_CONFIG, "backend_mode": "remote_api"})

        with pytest.raises(RemoteAPIConfigError, match="remote_api_base_url"):
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
        # Persist a config with mode=remote_api (but missing remote settings,
        # which would normally raise), and force local_cpu via the arg.
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
        fake_keyring,  # noqa: ARG002
    ) -> None:
        _seed_remote_config()

        backend = get_backend()  # picks up remote_api from disk

        assert isinstance(backend, RemoteOpenAICompatBackend)

    def test_model_arg_is_passed_through_to_local_backend(
        self,
        isolated_config_dir,  # noqa: ARG002
    ) -> None:
        backend = get_backend(model="large-v3", mode="local_cpu")

        assert backend.model_name == "large-v3"

    def test_model_arg_is_passed_through_to_remote_backend(
        self,
        isolated_config_dir,  # noqa: ARG002
        fake_keyring,  # noqa: ARG002
    ) -> None:
        # CLI uses --model to override per invocation without touching config.
        _seed_remote_config()

        backend = get_backend(model="distil-whisper-large-v3", mode="remote_api")

        assert backend.model_name == "distil-whisper-large-v3"
