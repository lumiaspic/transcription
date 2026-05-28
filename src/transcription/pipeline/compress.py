"""Pre-compress oversize audio files before uploading to a remote API.

Most OpenAI-compatible providers cap single-request uploads at 25 MB (Groq,
OpenAI). A 3-hour FLAC easily blows past that, even though the speech inside
is fine for Whisper at a fraction of the bitrate. Opus mono 16 kHz @ ~16 kbps
is the codec Groq's own docs recommend for speech — transparent for Whisper
and roughly 10× smaller than 16-bit PCM FLAC.

The compressed file is cached next to the original (e.g. `mic.opus` beside
`mic.flac`) so:
  - retries after a 4xx don't re-encode;
  - a fallback to a second remote backend in the chain reuses the same encode;
  - the original recording stays untouched on disk.

`ffmpeg` is required on PATH. Surfaced as a clear error rather than letting
the subprocess fail opaquely deep inside the request path.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


class CompressionError(RuntimeError):
    """ffmpeg failed or is not installed. Surfaces a one-liner that points
    the user at the most likely fix (install ffmpeg / put it on PATH)."""


def _ffmpeg_path() -> str:
    p = shutil.which("ffmpeg")
    if not p:
        raise CompressionError(
            "ffmpeg not found on PATH. Install it (winget install Gyan.FFmpeg, "
            "brew install ffmpeg, or apt install ffmpeg) so the remote backend "
            "can pre-compress oversize recordings."
        )
    return p


_CODEC_EXT = {
    "opus": "opus",
    "libopus": "opus",
    "mp3": "mp3",
    "aac": "m4a",
}


def _output_path(src: Path, codec: str) -> Path:
    ext = _CODEC_EXT.get(codec, codec)
    return src.with_suffix(f".{ext}")


def _encoder_for(codec: str) -> str:
    # `opus` is the friendlier user-facing name; ffmpeg's encoder is `libopus`.
    if codec == "opus":
        return "libopus"
    return codec


def compress_to_opus(
    src: Path,
    dst: Path,
    *,
    codec: str = "opus",
    bitrate: str = "16k",
    sample_rate: int = 16000,
) -> Path:
    """Encode `src` to `dst` as mono `codec` at `bitrate`. Returns `dst`."""
    ffmpeg = _ffmpeg_path()
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-y",  # overwrite (we already decided to re-encode)
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-c:a",
        _encoder_for(codec),
        "-b:a",
        bitrate,
        str(dst),
    ]
    log.info("ffmpeg compress: %s -> %s (%s @ %s)", src.name, dst.name, codec, bitrate)
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        raise CompressionError(
            f"ffmpeg failed (exit {e.returncode}): {e.stderr.strip()[:500]}"
        ) from e
    return dst


def maybe_compress_for_upload(
    src: Path,
    *,
    max_mb: float = 20,
    codec: str = "opus",
    bitrate: str = "16k",
) -> Path:
    """Return a path safe to upload — the original if under `max_mb`, or a
    cached compressed sibling otherwise.

    The cache check uses mtime: if the compressed file is newer than the
    source, it's reused. If the source was re-recorded (mtime bumped), the
    encode runs again.
    """
    try:
        size_mb = src.stat().st_size / (1024 * 1024)
    except OSError:
        return src
    if size_mb <= max_mb:
        return src

    dst = _output_path(src, codec)
    if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
        log.info(
            "Reusing cached compressed file %s (%.1f MB) for %s (%.1f MB > %.0f MB cap)",
            dst.name,
            dst.stat().st_size / (1024 * 1024),
            src.name,
            size_mb,
            max_mb,
        )
        return dst

    log.info(
        "Source %s is %.1f MB (cap %.0f MB) — compressing to %s @ %s",
        src.name,
        size_mb,
        max_mb,
        codec,
        bitrate,
    )
    return compress_to_opus(src, dst, codec=codec, bitrate=bitrate)
