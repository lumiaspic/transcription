"""Tests for `pipeline.health` — startup health checks.

Each check takes either a HardwareProbe or reads config/keyring/db.
We construct probes directly (not via `detect()`) to get pure isolation,
and use the shared `isolated_config_dir` + `fake_keyring` fixtures.
"""

from __future__ import annotations

from transcription.pipeline.hardware import HardwareProbe
from transcription.pipeline.health import (
    HealthItem,
    Severity,
    run_health_checks,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _probe(
    *,
    python_version: str = "3.11.12",
    has_torch: bool = True,
    has_cuda: bool = False,
    has_mps: bool = False,
    gpu_name: str | None = None,
    vram_gb: float | None = None,
    cuda_version: str | None = None,
    driver_version: str | None = None,
) -> HardwareProbe:
    return HardwareProbe(
        python_version=python_version,
        platform="test-platform",
        cpu_cores=4,
        has_torch=has_torch,
        torch_version="2.7.1" if has_torch else None,
        has_cuda=has_cuda,
        gpu_name=gpu_name,
        vram_gb=vram_gb,
        cuda_version=cuda_version,
        driver_version=driver_version,
        has_mps=has_mps,
    )


def _by_name(items: list[HealthItem], name: str) -> HealthItem:
    return next(i for i in items if i.name == name)


# ---------------------------------------------------------------------------
# Python version check
# ---------------------------------------------------------------------------


class TestPythonCheck:
    def test_supported_version_is_ok(
        self,
        isolated_config_dir,
        fake_keyring,  # noqa: ARG002
    ) -> None:
        items = run_health_checks(_probe(python_version="3.11.12"))

        assert _by_name(items, "Python").severity is Severity.OK

    def test_python_3_9_is_too_old_and_errors(
        self,
        isolated_config_dir,
        fake_keyring,  # noqa: ARG002
    ) -> None:
        items = run_health_checks(_probe(python_version="3.9.18"))

        assert _by_name(items, "Python").severity is Severity.ERROR

    def test_python_3_13_is_too_new_and_errors(
        self,
        isolated_config_dir,
        fake_keyring,  # noqa: ARG002
    ) -> None:
        # Upper bound matters — whisperx / torch don't support 3.13 yet at the
        # time of writing.
        items = run_health_checks(_probe(python_version="3.13.0"))

        assert _by_name(items, "Python").severity is Severity.ERROR

    def test_unparseable_version_is_warn_not_crash(
        self,
        isolated_config_dir,
        fake_keyring,  # noqa: ARG002
    ) -> None:
        items = run_health_checks(_probe(python_version="weird-build"))

        assert _by_name(items, "Python").severity is Severity.WARN


# ---------------------------------------------------------------------------
# Torch / GPU checks
# ---------------------------------------------------------------------------


class TestTorchAndGpuChecks:
    def test_torch_missing_is_error(
        self,
        isolated_config_dir,
        fake_keyring,  # noqa: ARG002
    ) -> None:
        items = run_health_checks(_probe(has_torch=False))

        torch_item = _by_name(items, "PyTorch")
        assert torch_item.severity is Severity.ERROR
        assert "uv sync --extra transcribe" in torch_item.message

    def test_gpu_check_skipped_when_torch_missing(
        self,
        isolated_config_dir,
        fake_keyring,  # noqa: ARG002
    ) -> None:
        # No point grading the GPU situation if torch isn't even there.
        items = run_health_checks(_probe(has_torch=False))

        assert _by_name(items, "GPU").severity is Severity.WARN

    def test_torch_ok_no_gpu_is_warn_with_cpu_estimate(
        self,
        isolated_config_dir,
        fake_keyring,  # noqa: ARG002
    ) -> None:
        items = run_health_checks(_probe(has_torch=True, has_cuda=False, has_mps=False))

        gpu_item = _by_name(items, "GPU")
        assert gpu_item.severity is Severity.WARN
        # User-facing warning: how long it'll take on CPU.
        assert "CPU" in gpu_item.message

    def test_torch_ok_with_cuda_is_ok_with_gpu_details(
        self,
        isolated_config_dir,
        fake_keyring,  # noqa: ARG002
    ) -> None:
        items = run_health_checks(
            _probe(
                has_torch=True,
                has_cuda=True,
                gpu_name="RTX 5090",
                vram_gb=24.0,
                cuda_version="12.4",
                driver_version="555.42",
            )
        )

        gpu_item = _by_name(items, "GPU")
        assert gpu_item.severity is Severity.OK
        assert "RTX 5090" in gpu_item.message
        assert "24.0 GB" in gpu_item.message
        assert "12.4" in gpu_item.message
        assert "555.42" in gpu_item.message

    def test_torch_ok_with_mps_is_ok_with_apple_silicon_note(
        self,
        isolated_config_dir,
        fake_keyring,  # noqa: ARG002
    ) -> None:
        items = run_health_checks(_probe(has_torch=True, has_cuda=False, has_mps=True))

        gpu_item = _by_name(items, "GPU")
        assert gpu_item.severity is Severity.OK
        assert "MPS" in gpu_item.message
        # Be transparent: WhisperX still runs on CPU on macOS.
        assert "CPU" in gpu_item.message


# ---------------------------------------------------------------------------
# HuggingFace token check
# ---------------------------------------------------------------------------


class TestHfTokenCheck:
    def test_token_present_is_ok(
        self,
        isolated_config_dir,  # noqa: ARG002
        fake_keyring: dict[tuple[str, str], str],
    ) -> None:
        from transcription import config as cfg

        fake_keyring[(cfg.KEYRING_SERVICE, "huggingface")] = "hf_xxx"

        items = run_health_checks(_probe())

        assert _by_name(items, "HuggingFace token").severity is Severity.OK

    def test_token_missing_is_warn_with_setup_instructions(
        self,
        isolated_config_dir,  # noqa: ARG002
        fake_keyring,  # noqa: ARG002
    ) -> None:
        items = run_health_checks(_probe())

        item = _by_name(items, "HuggingFace token")
        assert item.severity is Severity.WARN
        assert "set-token huggingface" in item.message


# ---------------------------------------------------------------------------
# Backend mode + jobs DB
# ---------------------------------------------------------------------------


class TestBackendModeCheck:
    def test_unset_is_warn(
        self,
        isolated_config_dir,
        fake_keyring,  # noqa: ARG002
    ) -> None:
        items = run_health_checks(_probe())

        assert _by_name(items, "Backend mode").severity is Severity.WARN

    def test_set_is_ok_and_shows_mode(
        self,
        isolated_config_dir,  # noqa: ARG002
        fake_keyring,  # noqa: ARG002
    ) -> None:
        from transcription import config as cfg

        cfg.save_config({**cfg.DEFAULT_CONFIG, "backend_mode": "local_cpu"})

        items = run_health_checks(_probe())

        item = _by_name(items, "Backend mode")
        assert item.severity is Severity.OK
        assert "local_cpu" in item.message


class TestJobsDbCheck:
    def test_fresh_db_inits_jobs_table_then_check_is_ok(
        self,
        isolated_config_dir,  # noqa: ARG002
        fake_keyring,  # noqa: ARG002
    ) -> None:
        # Force the JobQueue to create the DB+schema (jobs table) first.
        from transcription.pipeline.jobs import JobQueue

        JobQueue()  # constructor creates the table

        items = run_health_checks(_probe())

        assert _by_name(items, "Jobs DB").severity is Severity.OK

    def test_db_with_no_jobs_table_yet_is_warn(
        self,
        isolated_config_dir,  # noqa: ARG002
        fake_keyring,  # noqa: ARG002
    ) -> None:
        # Create the DB file but WITHOUT the jobs table (e.g. a future schema
        # migration left the file dangling). Health must catch this.
        import sqlite3

        from transcription.paths import jobs_db

        db_path = jobs_db()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.close()  # creates an empty DB

        items = run_health_checks(_probe())

        item = _by_name(items, "Jobs DB")
        assert item.severity is Severity.WARN
        assert "will init on first use" in item.message


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


def test_run_health_checks_returns_six_items_on_local_mode(
    isolated_config_dir,
    fake_keyring,  # noqa: ARG002
) -> None:
    # Documented contract: Python, PyTorch, CUDA GPU, HuggingFace token,
    # Backend mode, Jobs DB. Six items by default — the Remote API row only
    # appears when backend_mode=remote_api. Order matters for doctor layout.
    items = run_health_checks(_probe())

    names = [i.name for i in items]
    assert names == [
        "Python",
        "PyTorch",
        "GPU",
        "HuggingFace token",
        "Backend mode",
        "Jobs DB",
    ]


def test_run_health_checks_calls_probe_detect_when_none_provided(
    isolated_config_dir,
    fake_keyring,  # noqa: ARG002
) -> None:
    # Default arg: hw=None should trigger HardwareProbe.detect(). We don't
    # care what it returns here — we just want zero crashes when omitted.
    items = run_health_checks()  # no probe passed

    assert len(items) == 6


class TestRemoteApiCheck:
    """The Remote API check is conditional: only surfaces when the user
    actually opted into remote_api mode, so doctor stays terse for local
    users (and they're the majority)."""

    def test_no_remote_api_row_when_mode_is_local(
        self,
        isolated_config_dir,  # noqa: ARG002
        fake_keyring,  # noqa: ARG002
    ) -> None:
        from transcription import config as cfg

        cfg.save_config({**cfg.DEFAULT_CONFIG, "backend_mode": "local_cpu"})

        items = run_health_checks(_probe())

        assert "Remote API" not in [i.name for i in items]

    def test_missing_config_is_error_listing_what_to_set(
        self,
        isolated_config_dir,  # noqa: ARG002
        fake_keyring,  # noqa: ARG002
    ) -> None:
        from transcription import config as cfg

        cfg.save_config({**cfg.DEFAULT_CONFIG, "backend_mode": "remote_api"})

        items = run_health_checks(_probe())
        item = _by_name(items, "Remote API")

        assert item.severity is Severity.ERROR
        # Every missing piece must be named so the user fixes them in one go.
        assert "remote_api_base_url" in item.message
        assert "remote_api_model" in item.message
        assert "remote_api" in item.message  # keyring token slot

    def test_complete_config_with_token_is_ok_showing_endpoint(
        self,
        isolated_config_dir,  # noqa: ARG002
        fake_keyring: dict[tuple[str, str], str],
    ) -> None:
        from transcription import config as cfg

        cfg.save_config(
            {
                **cfg.DEFAULT_CONFIG,
                "backend_mode": "remote_api",
                "remote_api_base_url": "https://api.groq.com/openai/v1",
                "remote_api_model": "whisper-large-v3",
            }
        )
        fake_keyring[(cfg.KEYRING_SERVICE, "remote_api")] = "sk-x"

        items = run_health_checks(_probe())
        item = _by_name(items, "Remote API")

        assert item.severity is Severity.OK
        # Assert on the full URL (not just the host) so the test is strict
        # AND so CodeQL doesn't flag this as `py/incomplete-url-substring-sanitization`
        # — this is a status-message assertion, not URL validation.
        assert "https://api.groq.com/openai/v1" in item.message
        assert "whisper-large-v3" in item.message

    def test_custom_token_service_is_respected(
        self,
        isolated_config_dir,  # noqa: ARG002
        fake_keyring: dict[tuple[str, str], str],
    ) -> None:
        # Users who keep multiple providers' keys in keyring can point the
        # backend at a custom slot. The check must follow that pointer.
        from transcription import config as cfg

        cfg.save_config(
            {
                **cfg.DEFAULT_CONFIG,
                "backend_mode": "remote_api",
                "remote_api_base_url": "https://api.openai.com/v1",
                "remote_api_model": "whisper-1",
                "remote_api_token_service": "openai",
            }
        )
        # Token stored under "openai", NOT "remote_api"
        fake_keyring[(cfg.KEYRING_SERVICE, "openai")] = "sk-openai"

        items = run_health_checks(_probe())

        assert _by_name(items, "Remote API").severity is Severity.OK
