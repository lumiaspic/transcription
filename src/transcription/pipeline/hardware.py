"""Probe machine capabilities at runtime.

Used by the health check (CLI `doctor` command) and the GUI first-run
wizard to recommend a backend mode without the user guessing.
"""

from __future__ import annotations

import multiprocessing
import platform
import sys
from dataclasses import dataclass


@dataclass
class HardwareProbe:
    python_version: str
    platform: str
    cpu_cores: int
    has_torch: bool
    torch_version: str | None
    has_cuda: bool
    gpu_name: str | None
    vram_gb: float | None
    cuda_version: str | None
    driver_version: str | None  # NVIDIA driver, if available

    @classmethod
    def detect(cls) -> HardwareProbe:
        has_torch = False
        torch_version = None
        has_cuda = False
        gpu_name = None
        vram_gb = None
        cuda_version = None
        driver_version = None

        try:
            import torch

            has_torch = True
            torch_version = torch.__version__
            has_cuda = torch.cuda.is_available()
            if has_cuda:
                gpu_name = torch.cuda.get_device_name(0)
                vram_gb = torch.cuda.get_device_properties(0).total_memory / 1024**3
                cuda_version = torch.version.cuda
        except ImportError:
            pass

        # NVIDIA driver version via nvidia-smi if available (informational only)
        try:
            import subprocess

            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if out.returncode == 0:
                driver_version = out.stdout.strip().splitlines()[0] or None
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

        return cls(
            python_version=sys.version.split()[0],
            platform=platform.platform(),
            cpu_cores=multiprocessing.cpu_count(),
            has_torch=has_torch,
            torch_version=torch_version,
            has_cuda=has_cuda,
            gpu_name=gpu_name,
            vram_gb=vram_gb,
            cuda_version=cuda_version,
            driver_version=driver_version,
        )

    def summary(self) -> str:
        """One-liner suitable for logs."""
        gpu = f"{self.gpu_name} {self.vram_gb:.1f}GB" if self.has_cuda else "no GPU"
        return f"{self.platform} | Py{self.python_version} | {self.cpu_cores}c | {gpu}"
