"""Tests for `paths` — per-user filesystem locations.

Uses the `isolated_config_dir` fixture (in conftest.py) so we never touch
the developer's real %APPDATA% / ~/.config during the test run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from transcription import paths


class TestConfigDir:
    def test_returns_appdata_subdir_when_env_var_is_set(self, isolated_config_dir: Path) -> None:
        # `isolated_config_dir` already monkeypatches APPDATA → tmp_path/appdata.
        # config_dir() must therefore return tmp_path/appdata/transcription.
        assert paths.config_dir() == isolated_config_dir

    def test_creates_the_directory_if_missing(self, isolated_config_dir: Path) -> None:
        assert not isolated_config_dir.exists()  # tmp_path is fresh

        result = paths.config_dir()

        assert result.exists() and result.is_dir()

    def test_falls_back_to_home_dot_config_when_appdata_unset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The Linux/macOS path: no APPDATA, use ~/.config/transcription.
        monkeypatch.delenv("APPDATA", raising=False)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        result = paths.config_dir()

        assert result == tmp_path / ".config" / "transcription"
        assert result.exists()


class TestConfigFile:
    def test_returns_config_toml_inside_config_dir(self, isolated_config_dir: Path) -> None:
        assert paths.config_file() == isolated_config_dir / "config.toml"


class TestJobsDb:
    def test_returns_jobs_db_inside_config_dir(self, isolated_config_dir: Path) -> None:
        assert paths.jobs_db() == isolated_config_dir / "jobs.db"


class TestLogsDir:
    def test_creates_logs_subdir_of_config_dir(self, isolated_config_dir: Path) -> None:
        result = paths.logs_dir()

        assert result == isolated_config_dir / "logs"
        assert result.is_dir()


class TestRecordingsDir:
    def test_defaults_to_cwd_recordings_when_no_config_override(
        self,
        isolated_config_dir: Path,  # noqa: ARG002 — implicit isolation
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # No config.toml on disk → load_config() returns DEFAULT_CONFIG where
        # recordings_dir is None → fallback to <cwd>/recordings.
        monkeypatch.chdir(tmp_path)

        result = paths.recordings_dir()

        assert result == tmp_path / "recordings"
        assert result.is_dir()

    def test_honors_recordings_dir_override_from_config(
        self,
        isolated_config_dir: Path,  # noqa: ARG002
        tmp_path: Path,
    ) -> None:
        # Write a real config.toml so load_config() picks up the override.
        from transcription import config as cfg

        target = tmp_path / "custom-recordings"
        cfg.save_config({**cfg.DEFAULT_CONFIG, "recordings_dir": str(target)})

        result = paths.recordings_dir()

        assert result == target
        assert result.is_dir()
