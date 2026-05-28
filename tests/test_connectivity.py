"""Tests for the wizard/settings credential checks.

HTTP is mocked by monkeypatching `httpx.get` so the tests stay hermetic
(no network). We only assert on the (ok, message) contract.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from transcription import connectivity


class _FakeResponse:
    def __init__(self, status_code: int, json_body: Any | None = None) -> None:
        self.status_code = status_code
        self._json = json_body

    def json(self) -> Any:
        if self._json is None:
            raise ValueError("no json body")
        return self._json


def _patch_get(monkeypatch: pytest.MonkeyPatch, resp: object) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> object:
        captured["url"] = url
        captured["headers"] = kwargs.get("headers", {})
        return resp

    monkeypatch.setattr(httpx, "get", fake_get)
    return captured


def _patch_get_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, **kwargs: Any) -> object:
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx, "get", fake_get)


# ---------------------------------------------------------------------------
# HuggingFace token
# ---------------------------------------------------------------------------


class TestHuggingFaceToken:
    def test_empty_token_short_circuits_without_network(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail_get(*a: Any, **k: Any) -> object:  # pragma: no cover - must not run
            raise AssertionError("should not hit the network")

        monkeypatch.setattr(httpx, "get", fail_get)
        ok, msg = connectivity.test_huggingface_token("   ")
        assert ok is False
        assert "No token" in msg

    def test_valid_token_returns_username(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cap = _patch_get(monkeypatch, _FakeResponse(200, {"name": "alice"}))
        ok, msg = connectivity.test_huggingface_token("hf_abc")
        assert ok is True
        assert "alice" in msg
        assert cap["headers"]["Authorization"] == "Bearer hf_abc"

    def test_valid_token_without_name_still_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_get(monkeypatch, _FakeResponse(200, {}))
        ok, msg = connectivity.test_huggingface_token("hf_abc")
        assert ok is True
        assert "valid" in msg.lower()

    def test_valid_token_with_unparseable_body_still_ok(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 200 but the body isn't valid JSON — we should swallow the error and
        # still report the token as valid rather than crashing.
        _patch_get(monkeypatch, _FakeResponse(200))
        ok, msg = connectivity.test_huggingface_token("hf_abc")
        assert ok is True
        assert "valid" in msg.lower()

    @pytest.mark.parametrize("status", [401, 403])
    def test_rejected_token(self, monkeypatch: pytest.MonkeyPatch, status: int) -> None:
        _patch_get(monkeypatch, _FakeResponse(status))
        ok, msg = connectivity.test_huggingface_token("bad")
        assert ok is False
        assert "rejected" in msg.lower()

    def test_unexpected_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_get(monkeypatch, _FakeResponse(500))
        ok, msg = connectivity.test_huggingface_token("hf_abc")
        assert ok is False
        assert "500" in msg

    def test_network_error_is_friendly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_get_raises(monkeypatch)
        ok, msg = connectivity.test_huggingface_token("hf_abc")
        assert ok is False
        assert "internet" in msg.lower()


# ---------------------------------------------------------------------------
# Remote endpoint
# ---------------------------------------------------------------------------


class TestRemoteConnection:
    def test_missing_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ok, msg = connectivity.test_remote_connection("", "key")
        assert ok is False
        assert "URL" in msg

    def test_missing_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ok, msg = connectivity.test_remote_connection("https://api.x/v1", "")
        assert ok is False
        assert "key" in msg.lower()

    def test_success_hits_models_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cap = _patch_get(monkeypatch, _FakeResponse(200, {"data": []}))
        ok, msg = connectivity.test_remote_connection("https://api.x/v1/", "key")
        assert ok is True
        assert cap["url"] == "https://api.x/v1/models"
        assert cap["headers"]["Authorization"] == "Bearer key"

    @pytest.mark.parametrize("status", [401, 403])
    def test_bad_key(self, monkeypatch: pytest.MonkeyPatch, status: int) -> None:
        _patch_get(monkeypatch, _FakeResponse(status))
        ok, msg = connectivity.test_remote_connection("https://api.x/v1", "key")
        assert ok is False
        assert "key" in msg.lower()

    def test_404_is_treated_as_reachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_get(monkeypatch, _FakeResponse(404))
        ok, msg = connectivity.test_remote_connection("http://localhost:8080/v1", "key")
        assert ok is True
        assert "reachable" in msg.lower()

    def test_other_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_get(monkeypatch, _FakeResponse(500))
        ok, msg = connectivity.test_remote_connection("https://api.x/v1", "key")
        assert ok is False
        assert "500" in msg

    def test_network_error_is_friendly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_get_raises(monkeypatch)
        ok, msg = connectivity.test_remote_connection("https://api.x/v1", "key")
        assert ok is False
        assert "reach" in msg.lower()
