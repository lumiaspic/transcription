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

from ..config import get_token, load_config, resolve_remote_backend_config
from ..pipeline.compress import maybe_compress_for_upload
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

    Concrete subclasses split this into *recoverable* failures (the caller
    can try a different backend in the chain) and *terminal* failures (the
    request itself is malformed; retrying elsewhere won't help).
    """


# --- Recoverable: the fallback chain should try the next backend ---


class RecoverableRemoteAPIError(RemoteAPIRequestError):
    """Marker base for errors that justify trying the next backend in the
    fallback chain. The orchestrator catches this type specifically."""


class PayloadTooLarge(RecoverableRemoteAPIError):
    """HTTP 413 — file exceeds the provider's per-request upload cap."""


class RateLimited(RecoverableRemoteAPIError):
    """HTTP 429 — per-minute/per-hour throughput cap hit (Groq ASPH, etc.)."""


class QuotaExceeded(RecoverableRemoteAPIError):
    """Account-level quota gone (e.g. OpenAI 'insufficient_quota'). HTTP 429
    in OpenAI's case but semantically distinct from rate-limiting since
    waiting won't recover it — only a different account/backend will."""


# --- Terminal: do not retry on another backend ---


class AuthError(RemoteAPIRequestError):
    """HTTP 401 / 403 — bad or missing token. Other backends with their own
    tokens may still work, but the bug is local to this backend's config so
    we surface it instead of silently moving on."""


def _classify_http_error(status: int, body: str) -> RemoteAPIRequestError:
    """Map an HTTP error response to the most specific exception type.

    Falls back to plain `RemoteAPIRequestError` for unknown shapes so callers
    can still see the raw body — better than swallowing into a generic class.
    """
    body_lower = body.lower()
    msg = f"Remote API returned HTTP {status}: {body[:500]}"
    if status == 413:
        return PayloadTooLarge(msg)
    if status == 429:
        # OpenAI signals out-of-credits via the error code, not the status.
        # Distinguish it from "just rate-limited" so the chain can decide
        # whether waiting would help (it wouldn't, here).
        if "insufficient_quota" in body_lower or "quota" in body_lower:
            return QuotaExceeded(msg)
        return RateLimited(msg)
    if status in (401, 403):
        return AuthError(msg)
    return RemoteAPIRequestError(msg)


class RemoteOpenAICompatBackend(TranscriptionBackend):
    # `name` is set per-instance from `backend_name` so a chain that mixes
    # several remote providers (e.g. groq + openai) can attribute each
    # track to the right one in the merged transcript.

    def __init__(
        self,
        model: str | None = None,
        *,
        base_url: str | None = None,
        token_service: str | None = None,
        timeout_seconds: float | None = None,
        backend_name: str = "remote_api",
        max_upload_mb: float | None = None,
        compress_codec: str | None = None,
        compress_bitrate: str | None = None,
    ) -> None:
        cfg = load_config()
        resolved = resolve_remote_backend_config(backend_name, cfg)
        self.backend_name_id = backend_name
        # `self.name` is what gets serialized into TranscriptResult.backend.
        # Use the user-chosen identifier so the merged transcript can show
        # which chain entry handled which track (e.g. "groq" vs "openai").
        self.name = backend_name
        self.base_url = (base_url or resolved.get("base_url") or "").rstrip("/")
        self.model_name = model or resolved.get("model") or ""
        self.token_service = token_service or resolved.get("token_service") or backend_name
        self.timeout_seconds = float(
            timeout_seconds if timeout_seconds is not None else resolved.get("timeout_seconds", 600)
        )
        self.max_upload_mb = float(
            max_upload_mb if max_upload_mb is not None else cfg.get("remote_api_max_upload_mb", 20)
        )
        self.compress_codec = compress_codec or cfg.get("remote_api_compress_codec") or "opus"
        self.compress_bitrate = compress_bitrate or cfg.get("remote_api_compress_bitrate") or "16k"

        if not self.base_url:
            raise RemoteAPIConfigError(
                f"base_url is not set for backend '{backend_name}'. "
                f"Run: transcription config set remote_api_base_url "
                f"https://api.groq.com/openai/v1"
            )
        if not self.model_name:
            raise RemoteAPIConfigError(
                f"model is not set for backend '{backend_name}'. "
                f"Run: transcription config set remote_api_model whisper-large-v3"
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

        # Pre-compress oversize files transparently. The compressed file is
        # cached next to the original so a fallback to another remote backend
        # in the chain reuses the same encode (the codec/bitrate choice is
        # provider-agnostic — Whisper resamples to 16 kHz internally either
        # way).
        upload_path = maybe_compress_for_upload(
            audio_path,
            max_mb=self.max_upload_mb,
            codec=self.compress_codec,
            bitrate=self.compress_bitrate,
        )

        # Build multipart body. `verbose_json` is what unlocks per-segment
        # timestamps; without it we'd only get a flat `text`.
        data: dict[str, str] = {
            "model": self.model_name,
            "response_format": "verbose_json",
        }
        if language:
            data["language"] = language

        log.info(
            "Remote transcribe via %s (model=%s, profile=%s, file=%s)",
            self.base_url,
            self.model_name,
            profile.value,
            upload_path.name,
        )

        try:
            with upload_path.open("rb") as f:
                files = {"file": (upload_path.name, f, "application/octet-stream")}
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
            raise _classify_http_error(response.status_code, response.text)

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
                "uploaded_file": upload_path.name,
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
