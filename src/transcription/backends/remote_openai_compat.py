"""Remote transcription backend for any OpenAI-compatible API.

A single backend covers OpenAI, Groq, and most self-hosted Whisper servers
(whisper.cpp's `server`, faster-whisper-server, vLLM, …) because they all
expose the same endpoint shape:

    POST {base_url}/audio/transcriptions
    Authorization: Bearer <token>
    Content-Type: multipart/form-data
        file: <audio>
        model: <model>
        response_format: verbose_json
        language: <iso-639-1>   (optional)

The verbose_json response has a stable shape across providers — a top-level
`language` / `duration` plus a `segments[]` list with `start`, `end`, `text`.

Diarization is NOT part of the OpenAI API surface, so MULTI profile
transparently falls back to single-speaker with a warning. The user's MIC
track is already SOLO by convention; the SYSTEM track loses speaker
separation, which is the documented trade-off for the remote path.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from ..config import get_token, load_config
from ..pipeline.speakers import SpeakerProfile
from .base import Segment, TranscriptionBackend, TranscriptResult

log = logging.getLogger(__name__)


class RemoteAPIConfigError(RuntimeError):
    """Backend mode is remote_api but required config is missing.

    Raised at backend construction (not at first request) so the worker fails
    fast with a clear message instead of timing out on an empty base URL.
    """


class RemoteAPIRequestError(RuntimeError):
    """The remote API returned an error or the request itself failed.

    Wraps both transport errors (DNS / connection / timeout) and HTTP-status
    errors (4xx / 5xx) into one type so callers don't have to know about
    httpx-internal exception hierarchy.
    """


class RemoteOpenAICompatBackend(TranscriptionBackend):
    name = "remote_openai_compat"

    def __init__(
        self,
        model: str | None = None,
        *,
        base_url: str | None = None,
        token_service: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        cfg = load_config()
        self.base_url = (base_url or cfg.get("remote_api_base_url") or "").rstrip("/")
        self.model_name = model or cfg.get("remote_api_model") or ""
        self.token_service = token_service or cfg.get("remote_api_token_service") or "remote_api"
        self.timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else cfg.get("remote_api_timeout_seconds", 600)
        )

        if not self.base_url:
            raise RemoteAPIConfigError(
                "remote_api_base_url is not set. "
                "Run: transcription config set remote_api_base_url "
                "https://api.groq.com/openai/v1"
            )
        if not self.model_name:
            raise RemoteAPIConfigError(
                "remote_api_model is not set. "
                "Run: transcription config set remote_api_model whisper-large-v3"
            )

    def _token(self) -> str:
        token = get_token(self.token_service)
        if not token:
            raise RemoteAPIConfigError(
                f"No API token stored for keyring service '{self.token_service}'. "
                f"Run: transcription config set-token {self.token_service}"
            )
        return token

    def transcribe(
        self,
        audio_path: Path,
        profile: SpeakerProfile,
        language: str | None = None,
    ) -> TranscriptResult:
        token = self._token()
        url = f"{self.base_url}/audio/transcriptions"

        # Build multipart body. `verbose_json` is what unlocks per-segment
        # timestamps; without it we'd only get a flat `text`.
        data: dict[str, str] = {
            "model": self.model_name,
            "response_format": "verbose_json",
        }
        if language:
            data["language"] = language

        log.info(
            "Remote transcribe via %s (model=%s, profile=%s)",
            self.base_url,
            self.model_name,
            profile.value,
        )

        try:
            with audio_path.open("rb") as f:
                files = {"file": (audio_path.name, f, "application/octet-stream")}
                response = httpx.post(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    data=data,
                    files=files,
                    timeout=self.timeout_seconds,
                )
        except httpx.HTTPError as e:
            raise RemoteAPIRequestError(f"Remote API request failed: {e}") from e

        if response.status_code >= 400:
            # Surface the server's message verbatim — providers usually return
            # JSON with a useful `error.message`, but body might also be plain
            # text from a misbehaving proxy. Either way the user wants to see it.
            body = response.text[:500]
            raise RemoteAPIRequestError(f"Remote API returned HTTP {response.status_code}: {body}")

        try:
            payload = response.json()
        except ValueError as e:
            raise RemoteAPIRequestError(
                f"Remote API returned non-JSON body: {response.text[:200]}"
            ) from e

        segments = self._extract_segments(payload)
        if profile == SpeakerProfile.MULTI:
            # OpenAI-compat surface has no diarization. Document the
            # degradation in logs AND in result.meta so the merge step can
            # warn the user too if it wants.
            log.warning(
                "MULTI profile requested but %s has no diarization — "
                "labelling all segments as a single speaker.",
                self.base_url,
            )
        for s in segments:
            s.speaker = "SPEAKER_00"

        return TranscriptResult(
            language=str(payload.get("language", language or "unknown")),
            segments=segments,
            duration=float(payload.get("duration", 0.0)),
            backend=self.name,
            model=self.model_name,
            profile=profile,
            meta={
                "base_url": self.base_url,
                "diarization_skipped": profile == SpeakerProfile.MULTI,
            },
        )

    @staticmethod
    def _extract_segments(payload: dict) -> list[Segment]:
        out: list[Segment] = []
        for seg in payload.get("segments", []) or []:
            out.append(
                Segment(
                    start=float(seg.get("start", 0.0)),
                    end=float(seg.get("end", 0.0)),
                    text=str(seg.get("text", "")).strip(),
                )
            )
        # Fallback: some servers in `verbose_json` mode still return only a
        # flat `text` (no segments). Synthesize a single segment so downstream
        # code keeps working instead of producing an empty transcript.
        if not out and payload.get("text"):
            out.append(
                Segment(
                    start=0.0,
                    end=float(payload.get("duration", 0.0)),
                    text=str(payload["text"]).strip(),
                )
            )
        return out
