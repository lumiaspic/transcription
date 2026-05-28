"""Lightweight credential / endpoint checks for the setup UI.

These power the "Test token" / "Test connection" buttons in the first-run
wizard and the Settings dialog. Each returns a `(ok, message)` pair with a
human-readable message safe to show non-technical users — no stack traces,
no raw HTTP dumps.
"""

from __future__ import annotations

import httpx

_HF_WHOAMI = "https://huggingface.co/api/whoami-v2"


def test_huggingface_token(token: str, *, timeout: float = 15.0) -> tuple[bool, str]:
    """Validate a HuggingFace token against the whoami endpoint."""
    token = (token or "").strip()
    if not token:
        return False, "No token entered."
    try:
        resp = httpx.get(
            _HF_WHOAMI,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
    except httpx.HTTPError:
        return False, "Could not reach huggingface.co — check your internet connection."

    if resp.status_code == 200:
        name = ""
        try:
            name = resp.json().get("name", "")
        except Exception:
            pass
        return True, f"Token valid — signed in as {name}." if name else "Token valid."
    if resp.status_code in (401, 403):
        return False, "Token rejected — copy a fresh token from huggingface.co/settings/tokens."
    return False, f"Unexpected response from HuggingFace (HTTP {resp.status_code})."


def test_remote_connection(base_url: str, token: str, *, timeout: float = 15.0) -> tuple[bool, str]:
    """Probe an OpenAI-compatible endpoint's /models route with the token."""
    base_url = (base_url or "").strip().rstrip("/")
    token = (token or "").strip()
    if not base_url:
        return False, "No endpoint URL entered."
    if not token:
        return False, "No API key entered."

    url = f"{base_url}/models"
    try:
        resp = httpx.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
    except httpx.HTTPError:
        return False, f"Could not reach {base_url} — check the URL and your connection."

    if resp.status_code == 200:
        return True, "Connected — endpoint and API key look good."
    if resp.status_code in (401, 403):
        return False, "API key rejected by the server — double-check it."
    if resp.status_code == 404:
        # whisper.cpp / some self-hosted servers don't expose /models. The host
        # answered, so the URL is reachable; we just can't auto-verify the key.
        return (
            True,
            "Endpoint reachable (couldn't auto-verify the key — that's fine for some servers).",
        )
    return False, f"Server answered with HTTP {resp.status_code}."
