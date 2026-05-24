"""Tests for `export.format` — serialization of a TranscriptResult to .txt/.srt/.json.

Uses `tmp_path` (pytest builtin) to write files in an isolated temp dir
per test, no manual cleanup needed.
"""

from __future__ import annotations

import json
from pathlib import Path

from transcription.backends.base import TranscriptResult
from transcription.export.format import to_json, to_srt, to_txt, write_all


class TestToTxt:
    def test_writes_one_line_per_segment_with_trailing_newline(
        self, tmp_path: Path, mic_result: TranscriptResult
    ) -> None:
        out = tmp_path / "mic.txt"

        to_txt(mic_result, out)

        # File ends with \n so it's POSIX-friendly and concatenates cleanly.
        content = out.read_text(encoding="utf-8")
        assert content.endswith("\n")
        assert content.splitlines() == ["Bonjour.", "Comment ça va ?"]

    def test_prefixes_speaker_when_present(
        self, tmp_path: Path, system_result: TranscriptResult
    ) -> None:
        out = tmp_path / "system.txt"

        to_txt(system_result, out)

        lines = out.read_text(encoding="utf-8").splitlines()
        assert lines[0].startswith("[SPEAKER_00] ")
        assert lines[1].startswith("[SPEAKER_01] ")


class TestToSrt:
    def test_srt_timestamps_format_is_hh_mm_ss_comma_ms(
        self, tmp_path: Path, mic_result: TranscriptResult
    ) -> None:
        out = tmp_path / "mic.srt"

        to_srt(mic_result, out)

        content = out.read_text(encoding="utf-8")
        # First segment: 0.0 → 2.5 should serialize as 00:00:00,000 → 00:00:02,500
        assert "00:00:00,000 --> 00:00:02,500" in content
        # Second segment: 3.0 → 5.2 → 00:00:03,000 → 00:00:05,200
        assert "00:00:03,000 --> 00:00:05,200" in content

    def test_srt_segments_are_numbered_from_one(
        self, tmp_path: Path, mic_result: TranscriptResult
    ) -> None:
        out = tmp_path / "mic.srt"

        to_srt(mic_result, out)

        content = out.read_text(encoding="utf-8")
        # SRT convention: blocks start at 1, not 0.
        assert content.startswith("1\n")
        assert "\n2\n" in content


class TestToJson:
    def test_json_round_trips_all_metadata_fields(
        self, tmp_path: Path, system_result: TranscriptResult
    ) -> None:
        out = tmp_path / "system.json"

        to_json(system_result, out)
        data = json.loads(out.read_text(encoding="utf-8"))

        assert data["language"] == "fr"
        assert data["duration"] == 5.0
        assert data["backend"] == "test"
        assert data["model"] == "tiny"
        assert data["profile"] == "multi"  # SpeakerProfile.MULTI serializes to its .value
        assert data["track"] == "system"
        assert len(data["segments"]) == 3
        assert data["segments"][0] == {
            "start": 1.0,
            "end": 2.0,
            "text": "Salut.",
            "speaker": "SPEAKER_00",
        }

    def test_json_preserves_unicode_without_escaping(
        self, tmp_path: Path, mic_result: TranscriptResult
    ) -> None:
        out = tmp_path / "mic.json"

        to_json(mic_result, out)

        # ensure_ascii=False: "ça" stays as "ça", not "ça".
        raw = out.read_text(encoding="utf-8")
        assert "ça" in raw


class TestWriteAll:
    def test_creates_three_files_with_expected_extensions(
        self, tmp_path: Path, mic_result: TranscriptResult
    ) -> None:
        target_dir = tmp_path / "rec_001"  # does not exist yet, write_all must create it

        paths = write_all(mic_result, target_dir, stem="mic")

        assert set(paths.keys()) == {"txt", "srt", "json"}
        for kind, path in paths.items():
            assert path.exists(), f"{kind} file was not created"
            assert path.suffix == f".{kind}"
            assert path.parent == target_dir
