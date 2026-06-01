"""Tests for the dual-track recorder's format handling.

The hardware path (soundcard devices) is mocked at the boundary: `_stream_track`
is driven with a fake recorder context manager that yields numpy chunks, so the
real libsndfile encode runs and we can assert the on-disk file is a valid
Opus / FLAC / WAV stream that reads back frame-for-frame. Format selection and
validation (the parts that don't touch audio devices) are tested directly.
"""

from __future__ import annotations

import contextlib
import threading
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from transcription.audio import recorder
from transcription.audio.recorder import (
    _FORMAT_SPECS,
    SUPPORTED_FORMATS,
    DualRecorder,
    TrackSpec,
    _stream_track,
)

# Some libsndfile builds ship without the Opus codec; skip those params rather
# than fail. PyPI soundfile wheels (>=0.12) bundle it, so this is just defence.
_OPUS_OK = sf.check_format("OGG", "OPUS")
_FORMAT_PARAMS = [
    pytest.param(
        "opus",
        marks=pytest.mark.skipif(not _OPUS_OK, reason="libsndfile built without Opus"),
    ),
    "flac",
    "wav",
]


class _FakeRecorder:
    """Yields `n_chunks` constant-amplitude blocks, then trips `stop`.

    Mirrors the slice of soundcard's recorder API that `_stream_track` uses:
    a single `record(numframes)` call returning a (frames, channels) float32
    array. The `stop` event is set once the planned chunks are exhausted so the
    stream loop exits cleanly, just like the real Stop button would.
    """

    def __init__(
        self, stop: threading.Event, channels: int, n_chunks: int, *, noise: bool = False
    ) -> None:
        self._stop = stop
        self._channels = channels
        self._remaining = n_chunks
        # Seeded so the bytes are identical across formats in the size test.
        self._rng = np.random.default_rng(0) if noise else None

    def record(self, numframes: int) -> np.ndarray:
        if self._rng is not None:
            block = (self._rng.standard_normal((numframes, self._channels)) * 0.3).astype("float32")
        else:
            block = np.full((numframes, self._channels), 0.1, dtype="float32")
        self._remaining -= 1
        if self._remaining <= 0:
            self._stop.set()
        return block


def _spec_for(
    out_path: Path,
    fmt: str,
    channels: int,
    stop: threading.Event,
    *,
    n_chunks: int = 5,
    noise: bool = False,
) -> TrackSpec:
    sf_format, subtype, _ext = _FORMAT_SPECS[fmt]
    fake = _FakeRecorder(stop, channels, n_chunks=n_chunks, noise=noise)
    return TrackSpec(
        label="mic",
        recorder_cm=contextlib.nullcontext(fake),
        out_path=out_path,
        channels=channels,
        sample_rate=16_000,
        sf_format=sf_format,
        subtype=subtype,
    )


class TestStreamTrack:
    @pytest.mark.parametrize("fmt", _FORMAT_PARAMS)
    @pytest.mark.parametrize("channels", [1, 2])
    def test_writes_readable_audio(self, tmp_path: Path, fmt: str, channels: int) -> None:
        sf_format, _subtype, ext = _FORMAT_SPECS[fmt]
        out = tmp_path / f"mic.{ext}"
        stop = threading.Event()
        chunk_frames = int(16_000 * recorder.CHUNK_SECONDS)

        _stream_track(_spec_for(out, fmt, channels, stop), stop)

        assert out.exists()
        info = sf.info(str(out))
        assert info.format == sf_format
        assert info.samplerate == 16_000
        assert info.channels == channels
        # libsndfile compensates Opus encoder delay, so the frame count is exact.
        assert info.frames == 5 * chunk_frames

    @pytest.mark.skipif(not _OPUS_OK, reason="libsndfile built without Opus")
    def test_opus_is_much_smaller_than_flac(self, tmp_path: Path) -> None:
        # On noisy (near-incompressible) audio, Opus' lossy encode is far
        # smaller than FLAC's lossless one — the whole point of issue #53.
        opus_path = tmp_path / "mic.opus"
        flac_path = tmp_path / "mic.flac"
        for fmt, path in (("opus", opus_path), ("flac", flac_path)):
            stop = threading.Event()
            _stream_track(
                _spec_for(path, fmt, channels=1, stop=stop, n_chunks=20, noise=True), stop
            )

        assert opus_path.stat().st_size < flac_path.stat().st_size / 2


class TestFormatSelection:
    def test_opus_is_default_and_supported(self) -> None:
        assert recorder.DEFAULT_FORMAT == "opus"
        assert SUPPORTED_FORMATS == ("opus", "flac", "wav")

    def test_unsupported_format_rejected_before_touching_devices(self, tmp_path: Path) -> None:
        # Validation happens first, so an unknown format raises without ever
        # reaching the (hardware-bound) device lookup.
        with pytest.raises(ValueError, match="Unsupported format"):
            DualRecorder(tmp_path, format="mp3")
