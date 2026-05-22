"""Local WhisperX backend (GPU or CPU)."""
from __future__ import annotations

import logging
from pathlib import Path

from ..config import get_token, load_config
from ..pipeline.speakers import SpeakerProfile
from .base import DiarizationUnavailable, Segment, TranscriptionBackend, TranscriptResult

log = logging.getLogger(__name__)


class WhisperXLocalBackend(TranscriptionBackend):
    name = "whisperx_local"

    def __init__(self, model: str | None = None) -> None:
        cfg = load_config()
        self.model_name = model or cfg["model"]
        self._asr_model = None  # lazy

    def _device(self) -> tuple[str, str]:
        import torch
        if torch.cuda.is_available():
            cfg = load_config()
            return "cuda", cfg["compute_type_cuda"]
        cfg = load_config()
        return "cpu", cfg["compute_type_cpu"]

    def _load_asr(self):
        if self._asr_model is None:
            import whisperx
            device, compute_type = self._device()
            log.info("Loading WhisperX model %s on %s (%s)", self.model_name, device, compute_type)
            self._asr_model = whisperx.load_model(
                self.model_name, device, compute_type=compute_type
            )
        return self._asr_model

    def transcribe(
        self,
        audio_path: Path,
        profile: SpeakerProfile,
        language: str | None = None,
    ) -> TranscriptResult:
        import whisperx
        device, _ = self._device()
        model = self._load_asr()

        audio = whisperx.load_audio(str(audio_path))
        duration = len(audio) / 16_000

        log.info("Transcribing %s (%.1fs, profile=%s)", audio_path.name, duration, profile.value)
        result = model.transcribe(audio, language=language, batch_size=16)
        lang = result.get("language", "unknown")

        # Word-level alignment for accurate timestamps. Needed for proper
        # speaker assignment in MULTI mode and gives better SRT output anyway.
        try:
            align_model, align_meta = whisperx.load_align_model(language_code=lang, device=device)
            result = whisperx.align(
                result["segments"], align_model, align_meta, audio, device,
                return_char_alignments=False,
            )
        except Exception as e:
            # No alignment model for this language is non-fatal; keep coarse timestamps.
            log.warning("Alignment skipped for language=%s: %s", lang, e)

        segments = self._extract_segments(result)

        if profile == SpeakerProfile.MULTI:
            segments = self._apply_diarization(audio, result, segments, device)
        else:
            # SOLO: single speaker, label all segments
            for s in segments:
                s.speaker = "SPEAKER_00"

        return TranscriptResult(
            language=lang,
            segments=segments,
            duration=duration,
            backend=self.name,
            model=self.model_name,
            profile=profile,
        )

    @staticmethod
    def _extract_segments(result: dict) -> list[Segment]:
        out: list[Segment] = []
        for seg in result.get("segments", []):
            out.append(Segment(
                start=float(seg.get("start", 0.0)),
                end=float(seg.get("end", 0.0)),
                text=str(seg.get("text", "")).strip(),
            ))
        return out

    def _apply_diarization(
        self, audio, transcribe_result: dict, segments: list[Segment], device: str,
    ) -> list[Segment]:
        token = get_token("huggingface")
        if not token:
            raise DiarizationUnavailable(
                "No HuggingFace token configured. Run: transcription config set-token huggingface"
            )
        try:
            import whisperx
            log.info("Running pyannote diarization...")
            # WhisperX 3.8.x: param is `token=`, default model is
            # pyannote/speaker-diarization-community-1.
            diarize_pipeline = whisperx.diarize.DiarizationPipeline(
                token=token, device=device,
            )
            diarize_segments = diarize_pipeline(audio)
            assigned = whisperx.assign_word_speakers(diarize_segments, transcribe_result)
        except Exception as e:
            raise DiarizationUnavailable(f"Diarization failed: {e}") from e

        # Re-extract segments; assign_word_speakers adds a 'speaker' key.
        out: list[Segment] = []
        for seg in assigned.get("segments", []):
            out.append(Segment(
                start=float(seg.get("start", 0.0)),
                end=float(seg.get("end", 0.0)),
                text=str(seg.get("text", "")).strip(),
                speaker=seg.get("speaker"),
            ))
        return out
