"""Tests for `config` — TOML config file + OS keyring secrets.

All tests use:
- `isolated_config_dir` → redirect %APPDATA% to a tmp dir
- `fake_keyring`        → in-memory dict, no real OS keyring access
"""

from __future__ import annotations

from pathlib import Path

import pytest

from transcription import config as cfg

# ---------------------------------------------------------------------------
# Plain config (TOML)
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_returns_defaults_when_no_file_exists(
        self,
        isolated_config_dir: Path,  # noqa: ARG002 — implicit isolation
    ) -> None:
        result = cfg.load_config()

        # Must be a *copy* of DEFAULT_CONFIG, not the original dict — otherwise
        # callers mutating the result would corrupt the module-level constant.
        assert result == cfg.DEFAULT_CONFIG
        assert result is not cfg.DEFAULT_CONFIG

    def test_merges_user_values_over_defaults(self, isolated_config_dir: Path) -> None:
        # Manually create a config.toml with a partial override.
        config_path = isolated_config_dir / "config.toml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text('model = "large-v3"\nlanguage = "fr"\n', encoding="utf-8")

        result = cfg.load_config()

        assert result["model"] == "large-v3"  # overridden
        assert result["language"] == "fr"  # overridden
        assert result["backend"] == cfg.DEFAULT_CONFIG["backend"]  # not overridden

    def test_unknown_keys_in_file_are_preserved(self, isolated_config_dir: Path) -> None:
        # Forward-compat: a key written by a newer version of the app should
        # survive a load/save round-trip, not be silently dropped.
        config_path = isolated_config_dir / "config.toml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("experimental_flag = true\n", encoding="utf-8")

        result = cfg.load_config()

        assert result["experimental_flag"] is True


class TestSaveConfig:
    def test_writes_toml_that_load_can_read_back(
        self,
        isolated_config_dir: Path,  # noqa: ARG002
    ) -> None:
        original = {**cfg.DEFAULT_CONFIG, "model": "medium", "recording_sample_rate": 44100}

        cfg.save_config(original)
        roundtripped = cfg.load_config()

        assert roundtripped["model"] == "medium"
        assert roundtripped["recording_sample_rate"] == 44100

    def test_strips_none_values_before_serializing(self, isolated_config_dir: Path) -> None:
        # tomli-w can't serialize None — save_config must drop those keys
        # silently rather than crash. The defaults can re-supply them at load.
        cfg.save_config({"model": "small", "language": None, "recordings_dir": None})

        on_disk = (isolated_config_dir / "config.toml").read_text(encoding="utf-8")
        assert "model" in on_disk
        assert "language" not in on_disk
        assert "recordings_dir" not in on_disk


class TestSetConfigKey:
    def test_updates_single_key_and_preserves_others(
        self,
        isolated_config_dir: Path,  # noqa: ARG002
    ) -> None:
        cfg.save_config({**cfg.DEFAULT_CONFIG, "model": "small"})

        cfg.set_config_key("model", "large-v3")

        reloaded = cfg.load_config()
        assert reloaded["model"] == "large-v3"
        # Other defaults must still be there.
        assert reloaded["backend"] == cfg.DEFAULT_CONFIG["backend"]


# ---------------------------------------------------------------------------
# Token secrets (keyring)
# ---------------------------------------------------------------------------


class TestSetToken:
    def test_stores_token_under_app_service_namespace(
        self, fake_keyring: dict[tuple[str, str], str]
    ) -> None:
        cfg.set_token("huggingface", "hf_secret_abc")

        assert fake_keyring[(cfg.KEYRING_SERVICE, "huggingface")] == "hf_secret_abc"

    def test_rejects_empty_token(
        self,
        fake_keyring: dict[tuple[str, str], str],  # noqa: ARG002
    ) -> None:
        # Empty token = user pasted nothing. Better to refuse than to overwrite
        # an existing valid token with an empty string.
        with pytest.raises(ValueError, match="Empty token"):
            cfg.set_token("huggingface", "")


class TestGetToken:
    def test_returns_stored_token_when_present(
        self, fake_keyring: dict[tuple[str, str], str]
    ) -> None:
        fake_keyring[(cfg.KEYRING_SERVICE, "huggingface")] = "hf_xyz"

        assert cfg.get_token("huggingface") == "hf_xyz"

    def test_returns_none_when_token_not_set(
        self,
        fake_keyring: dict[tuple[str, str], str],  # noqa: ARG002
    ) -> None:
        assert cfg.get_token("never_stored") is None

    def test_keyring_error_is_swallowed_and_returns_none(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Simulate a flaky / unconfigured keyring backend. The app must not
        # crash on token lookup — it should warn and return None so the caller
        # can prompt the user to re-enter.
        import keyring
        import keyring.errors

        def _raise(*_args: object, **_kw: object) -> None:
            raise keyring.errors.KeyringError("backend unavailable")

        monkeypatch.setattr(keyring, "get_password", _raise)

        result = cfg.get_token("huggingface")

        assert result is None
        assert "keyring error" in capsys.readouterr().err


class TestRemoveToken:
    def test_returns_true_when_token_existed(
        self, fake_keyring: dict[tuple[str, str], str]
    ) -> None:
        fake_keyring[(cfg.KEYRING_SERVICE, "huggingface")] = "to_delete"

        assert cfg.remove_token("huggingface") is True
        assert (cfg.KEYRING_SERVICE, "huggingface") not in fake_keyring

    def test_returns_false_when_token_did_not_exist(
        self,
        fake_keyring: dict[tuple[str, str], str],  # noqa: ARG002
    ) -> None:
        # Idempotent: calling remove on an absent key isn't an error.
        assert cfg.remove_token("never_stored") is False


class TestListTokenStatus:
    def test_reports_presence_for_each_known_service(
        self, fake_keyring: dict[tuple[str, str], str]
    ) -> None:
        # Seed only one of the known services.
        fake_keyring[(cfg.KEYRING_SERVICE, "huggingface")] = "set"

        status = cfg.list_token_status()

        assert set(status.keys()) == set(cfg.KNOWN_TOKEN_SERVICES.keys())
        assert status["huggingface"] is True
        # Every other known service should be False (not set).
        for svc, present in status.items():
            if svc != "huggingface":
                assert present is False
