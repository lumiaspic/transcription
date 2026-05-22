"""Startup health checks.

Run at app start (CLI `doctor` and GUI startup) to surface config issues
early instead of letting them blow up the first job 15 seconds in.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..config import get_token, load_config
from ..paths import jobs_db
from .hardware import HardwareProbe


class Severity(str, Enum):
    OK = "ok"
    WARN = "warn"
    ERROR = "error"


@dataclass
class HealthItem:
    name: str
    severity: Severity
    message: str


def _check_python(hw: HardwareProbe) -> HealthItem:
    try:
        major, minor = (int(x) for x in hw.python_version.split(".")[:2])
    except ValueError:
        return HealthItem("Python", Severity.WARN, f"unparseable version {hw.python_version}")
    if (3, 10) <= (major, minor) < (3, 13):
        return HealthItem("Python", Severity.OK, hw.python_version)
    return HealthItem(
        "Python", Severity.ERROR,
        f"Need 3.10–3.12, got {hw.python_version}. WhisperX won't import.",
    )


def _check_torch(hw: HardwareProbe) -> HealthItem:
    if not hw.has_torch:
        return HealthItem(
            "PyTorch", Severity.ERROR,
            "Not installed. Run: uv sync --extra transcribe",
        )
    return HealthItem("PyTorch", Severity.OK, hw.torch_version or "?")


def _check_gpu(hw: HardwareProbe) -> HealthItem:
    if not hw.has_torch:
        return HealthItem("CUDA GPU", Severity.WARN, "skipped (PyTorch missing)")
    if hw.has_cuda:
        driver = f" (driver {hw.driver_version})" if hw.driver_version else ""
        return HealthItem(
            "CUDA GPU", Severity.OK,
            f"{hw.gpu_name}, {hw.vram_gb:.1f} GB VRAM, CUDA {hw.cuda_version}{driver}",
        )
    return HealthItem(
        "CUDA GPU", Severity.WARN,
        "Not available. Will run on CPU — expect ~30-60 min per 1 h of audio.",
    )


def _check_hf_token() -> HealthItem:
    if get_token("huggingface"):
        return HealthItem("HuggingFace token", Severity.OK, "stored in OS keyring")
    return HealthItem(
        "HuggingFace token", Severity.WARN,
        "Not set. Diarization will fall back to single-speaker on the system track. "
        "Run: transcription config set-token huggingface",
    )


def _check_backend_mode() -> HealthItem:
    mode = load_config().get("backend_mode")
    if mode:
        return HealthItem("Backend mode", Severity.OK, mode)
    return HealthItem(
        "Backend mode", Severity.WARN,
        "Not chosen yet. The GUI first-run wizard will ask on next launch.",
    )


def _check_jobs_db() -> HealthItem:
    try:
        import sqlite3
        conn = sqlite3.connect(jobs_db(), timeout=2.0)
        try:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            if not any(t[0] == "jobs" for t in tables):
                return HealthItem(
                    "Jobs DB", Severity.WARN,
                    f"{jobs_db()} exists but no 'jobs' table — will init on first use",
                )
            return HealthItem("Jobs DB", Severity.OK, str(jobs_db()))
        finally:
            conn.close()
    except Exception as e:
        return HealthItem("Jobs DB", Severity.ERROR, f"{jobs_db()}: {e}")


def run_health_checks(hw: HardwareProbe | None = None) -> list[HealthItem]:
    """Returns one HealthItem per check, in the order they should be shown."""
    hw = hw or HardwareProbe.detect()
    return [
        _check_python(hw),
        _check_torch(hw),
        _check_gpu(hw),
        _check_hf_token(),
        _check_backend_mode(),
        _check_jobs_db(),
    ]
