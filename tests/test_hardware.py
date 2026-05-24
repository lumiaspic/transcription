"""Tests for `pipeline.hardware` — runtime capability probe.

The probe tolerates missing torch / missing CUDA / missing nvidia-smi
(it's used by the doctor command which must work even on a broken setup).
Tests cover both the happy path (mock torch present) and the degraded
paths (torch absent → reported as missing instead of crashing).
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from transcription.pipeline.hardware import HardwareProbe


def _install_fake_torch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    version: str = "2.7.1+test",
    has_cuda: bool = False,
    gpu_name: str = "Fake RTX 5090",
    vram_bytes: int = 24 * 1024**3,
    cuda_version: str | None = "12.4",
) -> types.ModuleType:
    """Inject a fake `torch` module into sys.modules for the duration of the test."""
    fake = types.ModuleType("torch")
    fake.__version__ = version  # type: ignore[attr-defined]

    cuda = types.SimpleNamespace(
        is_available=lambda: has_cuda,
        get_device_name=lambda _idx: gpu_name,
        get_device_properties=lambda _idx: types.SimpleNamespace(total_memory=vram_bytes),
    )
    fake.cuda = cuda  # type: ignore[attr-defined]
    fake.version = types.SimpleNamespace(cuda=cuda_version)  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "torch", fake)
    return fake


def _disable_nvidia_smi(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make subprocess.run raise FileNotFoundError so the nvidia-smi probe is skipped."""
    import subprocess

    def _no_smi(*_args: Any, **_kw: Any) -> Any:
        raise FileNotFoundError("nvidia-smi not installed")

    monkeypatch.setattr(subprocess, "run", _no_smi)


# ---------------------------------------------------------------------------
# detect()
# ---------------------------------------------------------------------------


class TestDetect:
    def test_reports_basic_python_and_platform_info(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _disable_nvidia_smi(monkeypatch)

        probe = HardwareProbe.detect()

        # Python version is X.Y.Z, platform is non-empty, cpu_cores >= 1.
        assert probe.python_version.count(".") == 2
        assert probe.platform
        assert probe.cpu_cores >= 1

    def test_torch_absent_path_marks_torch_missing_without_crashing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Block any `import torch` by injecting None in sys.modules.
        monkeypatch.setitem(sys.modules, "torch", None)
        _disable_nvidia_smi(monkeypatch)

        probe = HardwareProbe.detect()

        # The whole point: doctor must run even on an install without the
        # `transcribe` extra. The probe degrades gracefully instead of raising.
        assert probe.has_torch is False
        assert probe.torch_version is None
        assert probe.has_cuda is False
        assert probe.gpu_name is None
        assert probe.vram_gb is None
        assert probe.cuda_version is None

    def test_torch_present_without_cuda(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_torch(monkeypatch, has_cuda=False)
        _disable_nvidia_smi(monkeypatch)

        probe = HardwareProbe.detect()

        assert probe.has_torch is True
        assert probe.torch_version == "2.7.1+test"
        assert probe.has_cuda is False
        # GPU details stay None when CUDA isn't available.
        assert probe.gpu_name is None
        assert probe.vram_gb is None

    def test_torch_with_cuda_populates_gpu_details(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_torch(
            monkeypatch,
            has_cuda=True,
            gpu_name="RTX 5090",
            vram_bytes=24 * 1024**3,
        )
        _disable_nvidia_smi(monkeypatch)

        probe = HardwareProbe.detect()

        assert probe.has_cuda is True
        assert probe.gpu_name == "RTX 5090"
        assert probe.vram_gb == pytest.approx(24.0, abs=0.01)
        assert probe.cuda_version == "12.4"

    def test_nvidia_smi_when_present_populates_driver_version(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess

        # Successful nvidia-smi response.
        fake_result = types.SimpleNamespace(returncode=0, stdout="555.42.06\n", stderr="")
        monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: fake_result)
        monkeypatch.setitem(sys.modules, "torch", None)

        probe = HardwareProbe.detect()

        assert probe.driver_version == "555.42.06"

    def test_nvidia_smi_failing_returncode_leaves_driver_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess

        fake_result = types.SimpleNamespace(returncode=1, stdout="", stderr="error")
        monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: fake_result)
        monkeypatch.setitem(sys.modules, "torch", None)

        probe = HardwareProbe.detect()

        assert probe.driver_version is None


# ---------------------------------------------------------------------------
# summary()
# ---------------------------------------------------------------------------


class TestSummary:
    def test_summary_includes_no_gpu_when_cuda_absent(self) -> None:
        probe = HardwareProbe(
            python_version="3.11.12",
            platform="Linux-test",
            cpu_cores=8,
            has_torch=True,
            torch_version="2.7.1",
            has_cuda=False,
            gpu_name=None,
            vram_gb=None,
            cuda_version=None,
            driver_version=None,
        )

        out = probe.summary()

        assert "no GPU" in out
        assert "3.11.12" in out
        assert "8c" in out

    def test_summary_includes_gpu_name_and_vram_when_cuda_present(self) -> None:
        probe = HardwareProbe(
            python_version="3.12.0",
            platform="Windows-11",
            cpu_cores=16,
            has_torch=True,
            torch_version="2.7.1",
            has_cuda=True,
            gpu_name="RTX 5090",
            vram_gb=24.0,
            cuda_version="12.4",
            driver_version="555.42",
        )

        out = probe.summary()

        assert "RTX 5090" in out
        assert "24.0GB" in out
