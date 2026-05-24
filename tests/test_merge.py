"""Tests for `export.merge` — chronological merge of multiple TranscriptResults.

Light integration test: exercises `merge_to_markdown` end-to-end including
its dependency on `speakers.namespace_speaker`.
"""

from __future__ import annotations

from pathlib import Path

from transcription.backends.base import TranscriptResult
from transcription.export.merge import merge_to_markdown


def test_segments_sorted_by_start_time_across_tracks(
    tmp_path: Path, mic_result: TranscriptResult, system_result: TranscriptResult
) -> None:
    out = tmp_path / "transcript.md"

    merge_to_markdown([mic_result, system_result], out)

    # Expected chronological order of start times across both tracks:
    #   mic[0]    = 0.0  → MIC
    #   system[0] = 1.0  → SYSTEM_S0
    #   system[1] = 2.5  → SYSTEM_S1
    #   mic[1]    = 3.0  → MIC
    #   system[2] = 4.5  → SYSTEM_S0
    body = out.read_text(encoding="utf-8")
    speaker_order = [
        line.split("**[")[1].split("]")[0] for line in body.splitlines() if line.startswith("**[")
    ]
    assert speaker_order == ["MIC", "SYSTEM_S0", "SYSTEM_S1", "MIC", "SYSTEM_S0"]


def test_header_includes_language_model_and_backend(
    tmp_path: Path, mic_result: TranscriptResult
) -> None:
    out = tmp_path / "transcript.md"

    merge_to_markdown([mic_result], out)

    body = out.read_text(encoding="utf-8")
    assert body.startswith("# Transcript")
    assert "Language: fr" in body
    assert "model: tiny" in body
    assert "backend: test" in body


def test_speaker_legend_lists_unique_namespaced_labels(
    tmp_path: Path, mic_result: TranscriptResult, system_result: TranscriptResult
) -> None:
    out = tmp_path / "transcript.md"

    merge_to_markdown([mic_result, system_result], out)

    body = out.read_text(encoding="utf-8")
    # Legend line lists each unique label exactly once, sorted alphabetically.
    assert "Speakers: `MIC`, `SYSTEM_S0`, `SYSTEM_S1`" in body


def test_empty_results_list_produces_minimal_but_valid_markdown(tmp_path: Path) -> None:
    # Edge case: caller passes []. Should not crash, should produce a
    # header-only file (no language line since there's no first result to
    # read it from).
    out = tmp_path / "transcript.md"

    merge_to_markdown([], out)

    body = out.read_text(encoding="utf-8")
    assert body.startswith("# Transcript")
    assert "Speakers:" not in body  # legend only emitted when there's at least one result


def test_result_with_zero_segments_does_not_appear_in_body(
    tmp_path: Path, mic_result: TranscriptResult
) -> None:
    # A track that transcribed to silence shouldn't leave artifacts in the
    # merged transcript — but its metadata is still used for the header.
    empty = TranscriptResult(
        language=mic_result.language,
        segments=[],
        duration=0.0,
        backend=mic_result.backend,
        model=mic_result.model,
        profile=mic_result.profile,
        track="system",
    )

    out = tmp_path / "transcript.md"
    merge_to_markdown([mic_result, empty], out)

    body = out.read_text(encoding="utf-8")
    assert "SYSTEM" not in body  # no segments for system → no SYSTEM speaker line emitted
    assert "MIC" in body
