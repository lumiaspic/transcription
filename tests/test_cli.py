"""Tests for the Typer CLI surface (`cli.py`).

Uses `typer.testing.CliRunner` which invokes commands in-process — no
subprocess overhead, full stdout capture, and ergonomic assertions.

Commands NOT covered here:
- `record` and `gui` — hardware-bound (DualRecorder, NiceGUI window)
- `daemon` — exercised in test_worker.py at the Worker level
- `devices` — hardware-bound (soundcard enumeration)
- `doctor` — exercised by test_hardware.py + test_health.py at unit level
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.conftest import FakeBackend
from transcription import cli as cli_mod
from transcription.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cli_env(
    isolated_config_dir: Path,  # noqa: ARG001 — implicit env isolation
    fake_keyring: dict[tuple[str, str], str],  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Path:
    """All-in-one CLI test isolation.

    - Redirects $APPDATA to a tmp dir (config + jobs.db live there).
    - Replaces the OS keyring with an in-memory store.
    - chdir into tmp_path so `recordings_dir()` defaults to ./recordings here.

    Yields the tmp_path so tests can drop fixture files in it.
    """
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# `list` (recordings)
# ---------------------------------------------------------------------------


class TestListRecordings:
    def test_empty_recordings_dir_shows_friendly_message(
        self,
        runner: CliRunner,
        cli_env: Path,  # noqa: ARG002
    ) -> None:
        result = runner.invoke(app, ["list"])

        assert result.exit_code == 0
        assert "No recordings" in result.stdout

    def test_lists_recordings_in_reverse_chronological_order(
        self, runner: CliRunner, cli_env: Path
    ) -> None:
        # The CLI sorts by name (which encodes the timestamp), descending.
        recordings = cli_env / "recordings"
        for rec_id in ("20260101_120000", "20260102_120000", "20260103_120000"):
            d = recordings / rec_id
            d.mkdir(parents=True)
            (d / "meta.json").write_text(
                json.dumps(
                    {
                        "id": rec_id,
                        "duration_seconds": 12.3,
                        "tracks": ["mic", "system"],
                        "transcribed": True,
                    }
                ),
                encoding="utf-8",
            )

        result = runner.invoke(app, ["list"])

        assert result.exit_code == 0
        # Newest first.
        assert result.stdout.index("20260103") < result.stdout.index("20260102")
        assert "yes" in result.stdout  # transcribed marker


# ---------------------------------------------------------------------------
# `transcribe` (synchronous)
# ---------------------------------------------------------------------------


class TestTranscribe:
    def test_missing_recording_id_exits_with_code_1(
        self,
        runner: CliRunner,
        cli_env: Path,  # noqa: ARG002
    ) -> None:
        result = runner.invoke(app, ["transcribe", "nonexistent_rec"])

        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()

    def test_runs_transcription_with_provided_recording_id(
        self,
        runner: CliRunner,
        cli_env: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Set up: real recording dir with a mic track + fake backend in cli.
        rec_id = "rec_xyz"
        rec_dir = cli_env / "recordings" / rec_id
        rec_dir.mkdir(parents=True)
        (rec_dir / "mic.flac").write_bytes(b"")

        fake = FakeBackend()
        monkeypatch.setattr(cli_mod, "get_backend", lambda model=None: fake)

        result = runner.invoke(app, ["transcribe", rec_id])

        assert result.exit_code == 0
        # Outputs were written.
        assert (rec_dir / "transcript.md").exists()
        assert (rec_dir / "mic.txt").exists()
        # User got the "Done" message.
        assert "Done" in result.stdout


# ---------------------------------------------------------------------------
# `recover`
# ---------------------------------------------------------------------------


class TestRecover:
    def test_no_orphans_prints_friendly_message(
        self,
        runner: CliRunner,
        cli_env: Path,  # noqa: ARG002
    ) -> None:
        result = runner.invoke(app, ["recover"])

        assert result.exit_code == 0
        assert "No orphan" in result.stdout

    def test_orphan_is_recovered_and_enqueued_by_default(
        self, runner: CliRunner, cli_env: Path
    ) -> None:
        # Create an orphan: dir with audio but no meta.json.
        import numpy as np
        import soundfile as sf

        rec_dir = cli_env / "recordings" / "orphaned_one"
        rec_dir.mkdir(parents=True)
        sf.write(
            str(rec_dir / "mic.wav"),
            np.zeros(16_000, dtype=np.float32),
            16_000,
            subtype="PCM_16",
        )

        result = runner.invoke(app, ["recover"])

        assert result.exit_code == 0
        assert "orphaned_one" in result.stdout
        assert (rec_dir / "meta.json").exists()


# ---------------------------------------------------------------------------
# `jobs ...`
# ---------------------------------------------------------------------------


class TestJobsList:
    def test_empty_queue_prints_friendly_message(
        self,
        runner: CliRunner,
        cli_env: Path,  # noqa: ARG002
    ) -> None:
        result = runner.invoke(app, ["jobs", "list"])

        assert result.exit_code == 0
        assert "No jobs" in result.stdout

    def test_lists_pending_jobs(self, runner: CliRunner, cli_env: Path) -> None:
        from transcription.pipeline.jobs import JobQueue

        JobQueue().enqueue(recording_id="rec_listed", recording_dir=cli_env / "rec_listed")

        result = runner.invoke(app, ["jobs", "list"])

        assert result.exit_code == 0
        assert "rec_listed" in result.stdout


class TestJobsShow:
    def test_unknown_id_exits_with_code_1(
        self,
        runner: CliRunner,
        cli_env: Path,  # noqa: ARG002
    ) -> None:
        result = runner.invoke(app, ["jobs", "show", "999999"])

        assert result.exit_code == 1
        assert "No job" in result.stdout

    def test_existing_id_prints_all_fields(self, runner: CliRunner, cli_env: Path) -> None:
        from transcription.pipeline.jobs import JobQueue

        job_id = JobQueue().enqueue(recording_id="rec_show", recording_dir=cli_env / "rec_show")

        result = runner.invoke(app, ["jobs", "show", str(job_id)])

        assert result.exit_code == 0
        assert "rec_show" in result.stdout
        assert "pending" in result.stdout


class TestJobsRetry:
    def test_failed_job_is_re_queued(self, runner: CliRunner, cli_env: Path) -> None:
        from transcription.pipeline.jobs import JobQueue

        queue = JobQueue()
        job_id = queue.enqueue(recording_id="rec_retry", recording_dir=cli_env / "x")
        queue.mark_failed(job_id, "boom")

        result = runner.invoke(app, ["jobs", "retry", str(job_id)])

        assert result.exit_code == 0
        assert "re-queued" in result.stdout
        assert queue.get(job_id).status == "pending"  # type: ignore[union-attr]

    def test_non_failed_job_is_not_re_queued(self, runner: CliRunner, cli_env: Path) -> None:
        from transcription.pipeline.jobs import JobQueue

        JobQueue().enqueue(recording_id="x", recording_dir=cli_env / "x")

        # Pending → can't be retried (it's already in line). CLI must say so
        # rather than silently misbehave.
        result = runner.invoke(app, ["jobs", "retry", "1"])

        assert result.exit_code == 0
        assert "could not be re-queued" in result.stdout


# ---------------------------------------------------------------------------
# `config ...`
# ---------------------------------------------------------------------------


class TestConfigShow:
    def test_lists_all_settings_and_token_statuses(
        self,
        runner: CliRunner,
        cli_env: Path,  # noqa: ARG002
    ) -> None:
        result = runner.invoke(app, ["config", "show"])

        assert result.exit_code == 0
        # A few sentinel keys we always emit.
        assert "model" in result.stdout
        assert "backend" in result.stdout
        # Token services from the known list show up with status.
        assert "huggingface" in result.stdout
        assert "not set" in result.stdout  # nothing seeded


class TestConfigSet:
    def test_writes_key_to_disk(self, runner: CliRunner, cli_env: Path) -> None:
        from transcription import config as cfg

        result = runner.invoke(app, ["config", "set", "model", "large-v3"])

        assert result.exit_code == 0
        assert "large-v3" in result.stdout
        assert cfg.load_config()["model"] == "large-v3"


class TestConfigUnset:
    def test_unsetting_an_unset_key_warns_without_failing(
        self,
        runner: CliRunner,
        cli_env: Path,  # noqa: ARG002
    ) -> None:
        result = runner.invoke(app, ["config", "unset", "backend_mode"])

        assert result.exit_code == 0
        assert "not set" in result.stdout

    def test_unsetting_a_set_key_clears_it(
        self,
        runner: CliRunner,
        cli_env: Path,  # noqa: ARG002
    ) -> None:
        from transcription import config as cfg

        cfg.save_config({**cfg.DEFAULT_CONFIG, "backend_mode": "local_cpu"})

        result = runner.invoke(app, ["config", "unset", "backend_mode"])

        assert result.exit_code == 0
        assert cfg.load_config().get("backend_mode") is None


class TestConfigSetToken:
    def test_stores_token_from_password_prompt(
        self,
        runner: CliRunner,
        cli_env: Path,  # noqa: ARG002
        fake_keyring: dict[tuple[str, str], str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # `getpass` is what CLI uses (so the token isn't echoed). Mock it.
        monkeypatch.setattr(cli_mod, "getpass", lambda _prompt: "hf_secret_token")

        result = runner.invoke(app, ["config", "set-token", "huggingface"])

        assert result.exit_code == 0
        assert "stored" in result.stdout.lower()
        from transcription import config as cfg

        assert fake_keyring[(cfg.KEYRING_SERVICE, "huggingface")] == "hf_secret_token"

    def test_empty_token_aborts_with_exit_code_1(
        self,
        runner: CliRunner,
        cli_env: Path,  # noqa: ARG002
        fake_keyring,  # noqa: ARG002
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(cli_mod, "getpass", lambda _prompt: "   ")  # whitespace only

        result = runner.invoke(app, ["config", "set-token", "huggingface"])

        assert result.exit_code == 1
        assert "aborted" in result.stdout.lower()

    def test_unknown_service_warns_but_still_stores_token(
        self,
        runner: CliRunner,
        cli_env: Path,  # noqa: ARG002
        fake_keyring: dict[tuple[str, str], str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Trade-off: we accept arbitrary service names (future-proofing) but
        # warn the user it's not one we recognize.
        monkeypatch.setattr(cli_mod, "getpass", lambda _prompt: "tok")

        result = runner.invoke(app, ["config", "set-token", "unknown_svc"])

        assert result.exit_code == 0
        assert "Unknown service" in result.stdout
        from transcription import config as cfg

        assert fake_keyring[(cfg.KEYRING_SERVICE, "unknown_svc")] == "tok"


class TestConfigRemoveToken:
    def test_existing_token_is_removed(
        self,
        runner: CliRunner,
        cli_env: Path,  # noqa: ARG002
        fake_keyring: dict[tuple[str, str], str],
    ) -> None:
        from transcription import config as cfg

        fake_keyring[(cfg.KEYRING_SERVICE, "huggingface")] = "to_delete"

        result = runner.invoke(app, ["config", "remove-token", "huggingface"])

        assert result.exit_code == 0
        assert "Removed" in result.stdout
        assert (cfg.KEYRING_SERVICE, "huggingface") not in fake_keyring

    def test_missing_token_warns_without_failing(
        self,
        runner: CliRunner,
        cli_env: Path,  # noqa: ARG002
        fake_keyring,  # noqa: ARG002
    ) -> None:
        # Idempotent: removing an absent token is a no-op, not an error.
        result = runner.invoke(app, ["config", "remove-token", "never_stored"])

        assert result.exit_code == 0
        assert "No token" in result.stdout


# ---------------------------------------------------------------------------
# Top-level: --help works (smoke test that all commands are wired)
# ---------------------------------------------------------------------------


def test_help_lists_all_top_level_commands(runner: CliRunner) -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for cmd in (
        "record",
        "transcribe",
        "list",
        "devices",
        "recover",
        "doctor",
        "gui",
        "daemon",
        "jobs",
        "config",
    ):
        assert cmd in result.stdout, f"command {cmd} missing from --help"
