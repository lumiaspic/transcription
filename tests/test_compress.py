"""Tests for `pipeline.compress` — Opus pre-compression for oversize uploads.

ffmpeg is mocked out by patching `shutil.which` + `subprocess.run`, so no real
encode happens. The tests focus on the decision logic:
  - under-threshold files are passed through untouched
  - over-threshold files trigger an encode to a sibling path
  - a cached compressed file is reused when newer than the source
  - missing ffmpeg raises a clear CompressionError
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from transcription.pipeline import compress
from transcription.pipeline.compress import (
    CompressionError,
    compress_to_opus,
    maybe_compress_for_upload,
)


def _write_file(path: Path, size_mb: float) -> Path:
    """Create a file padded to roughly `size_mb` megabytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * int(size_mb * 1024 * 1024))
    return path


@pytest.fixture
def fake_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Pretend ffmpeg is installed and record the command it would receive.

    The fake `run` writes a tiny placeholder file at the output path so the
    caller's mtime / existence checks behave realistically.
    """
    captured: dict[str, Any] = {"calls": []}

    monkeypatch.setattr(compress.shutil, "which", lambda _name: "/usr/bin/ffmpeg")

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["calls"].append(cmd)
        # The last positional arg is the output path. Create it so downstream
        # code sees a real file with a fresh mtime.
        Path(cmd[-1]).write_bytes(b"OggS")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(compress.subprocess, "run", fake_run)
    return captured


class TestMaybeCompressForUpload:
    def test_returns_original_when_under_threshold(self, tmp_path: Path) -> None:
        src = _write_file(tmp_path / "mic.flac", size_mb=1.0)

        result = maybe_compress_for_upload(src, max_mb=20)

        # No encode runs and the caller uploads the source as-is.
        assert result == src

    def test_invokes_ffmpeg_when_over_threshold(
        self, tmp_path: Path, fake_ffmpeg: dict[str, Any]
    ) -> None:
        src = _write_file(tmp_path / "mic.flac", size_mb=30)

        result = maybe_compress_for_upload(src, max_mb=20, codec="opus", bitrate="16k")

        # Compressed sibling — same stem, .opus extension.
        assert result == tmp_path / "mic.opus"
        assert result.exists()
        # ffmpeg saw mono / 16 kHz / Opus @ 16k.
        cmd = fake_ffmpeg["calls"][0]
        assert "-ac" in cmd and cmd[cmd.index("-ac") + 1] == "1"
        assert "-ar" in cmd and cmd[cmd.index("-ar") + 1] == "16000"
        assert "-c:a" in cmd and cmd[cmd.index("-c:a") + 1] == "libopus"
        assert "-b:a" in cmd and cmd[cmd.index("-b:a") + 1] == "16k"

    def test_reuses_cached_compressed_file_when_newer_than_source(
        self, tmp_path: Path, fake_ffmpeg: dict[str, Any]
    ) -> None:
        src = _write_file(tmp_path / "mic.flac", size_mb=30)
        # Pre-existing compressed file with a newer mtime — the cache hit path.
        dst = tmp_path / "mic.opus"
        dst.write_bytes(b"OggS")
        future = src.stat().st_mtime + 10
        os.utime(dst, (future, future))

        result = maybe_compress_for_upload(src, max_mb=20)

        assert result == dst
        # Crucially: no ffmpeg subprocess was launched.
        assert fake_ffmpeg["calls"] == []

    def test_reencodes_when_cache_is_older_than_source(
        self, tmp_path: Path, fake_ffmpeg: dict[str, Any]
    ) -> None:
        src = _write_file(tmp_path / "mic.flac", size_mb=30)
        dst = tmp_path / "mic.opus"
        dst.write_bytes(b"stale")
        # Stale cache: mtime older than source. Re-encode.
        past = src.stat().st_mtime - 10
        os.utime(dst, (past, past))

        maybe_compress_for_upload(src, max_mb=20)

        assert len(fake_ffmpeg["calls"]) == 1


class TestEdgeCases:
    def test_missing_source_file_returns_path_unchanged(self, tmp_path: Path) -> None:
        # stat() raises OSError on a path that doesn't exist — fall through
        # rather than crash, and let the upload itself produce the real error.
        missing = tmp_path / "vanished.flac"

        assert maybe_compress_for_upload(missing, max_mb=20) == missing

    def test_non_opus_codec_passes_through_encoder_name(
        self, tmp_path: Path, fake_ffmpeg: dict[str, Any]
    ) -> None:
        # Only "opus" gets remapped to "libopus"; other codecs are passed
        # to ffmpeg verbatim so users can pick e.g. mp3 without surprise.
        src = _write_file(tmp_path / "mic.flac", size_mb=30)

        maybe_compress_for_upload(src, max_mb=20, codec="mp3", bitrate="32k")

        cmd = fake_ffmpeg["calls"][0]
        assert cmd[cmd.index("-c:a") + 1] == "mp3"

    def test_opus_source_re_encodes_to_distinct_path(
        self, tmp_path: Path, fake_ffmpeg: dict[str, Any]
    ) -> None:
        # An oversize Opus recording re-encoded to mono Opus must NOT overwrite
        # itself in place (ffmpeg -y on its own input would destroy the only
        # recording). The upload copy lands at a distinct sibling.
        src = _write_file(tmp_path / "mic.opus", size_mb=30)

        result = maybe_compress_for_upload(src, max_mb=20)

        assert result == tmp_path / "mic.upload.opus"
        cmd = fake_ffmpeg["calls"][0]
        assert cmd[-1] == str(tmp_path / "mic.upload.opus")
        assert str(src) != cmd[-1]


class TestCompressToOpus:
    def test_raises_compression_error_when_ffmpeg_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Surface a one-liner pointing at the install path, not a deep
        # FileNotFoundError from subprocess.
        monkeypatch.setattr(compress.shutil, "which", lambda _name: None)
        src = _write_file(tmp_path / "mic.flac", size_mb=1)

        with pytest.raises(CompressionError, match="ffmpeg not found"):
            compress_to_opus(src, tmp_path / "out.opus")

    def test_raises_compression_error_on_nonzero_exit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(compress.shutil, "which", lambda _name: "/usr/bin/ffmpeg")

        def boom(_cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            raise subprocess.CalledProcessError(
                returncode=1, cmd=["ffmpeg"], stderr="invalid codec"
            )

        monkeypatch.setattr(compress.subprocess, "run", boom)
        src = _write_file(tmp_path / "mic.flac", size_mb=1)

        with pytest.raises(CompressionError, match="invalid codec"):
            compress_to_opus(src, tmp_path / "out.opus")
