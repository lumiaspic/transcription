"""Tests for `paths` — per-user filesystem locations.

Uses the `isolated_config_dir` fixture (in conftest.py) so we never touch
the developer's real config dir during the test run.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from transcription import paths


class TestConfigDir:
    def test_returns_platform_config_subdir(self, isolated_config_dir: Path) -> None:
        # `isolated_config_dir` redirects HOME (and APPDATA on Windows) to
        # a temp dir and returns the path config_dir() is expected to produce.
        assert paths.config_dir() == isolated_config_dir

    def test_creates_the_directory_if_missing(self, isolated_config_dir: Path) -> None:
        assert not isolated_config_dir.exists()  # tmp_path is fresh

        result = paths.config_dir()

        assert result.exists() and result.is_dir()

    def test_uses_library_application_support_on_macos(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        result = paths.config_dir()

        assert result == tmp_path / "Library" / "Application Support" / "transcription"
        assert result.exists()

    def test_uses_dot_config_on_linux(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        result = paths.config_dir()

        assert result == tmp_path / ".config" / "transcription"
        assert result.exists()

    def test_uses_appdata_on_windows(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        fake_appdata = tmp_path / "AppData" / "Roaming"
        monkeypatch.setenv("APPDATA", str(fake_appdata))

        result = paths.config_dir()

        assert result == fake_appdata / "transcription"
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
