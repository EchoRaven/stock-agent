import logging

import httpx
import pytest

import app.llm.gemini as mod
from app.llm.gemini import GeminiClient


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


def _text_payload(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def test_no_key_returns_none_with_warning(caplog):
    with caplog.at_level(logging.WARNING):
        out = GeminiClient(api_key="").generate_json("hello")
    assert out is None
    assert "gemini" in caplog.text.lower()


def test_generate_json_success(monkeypatch):
    captured = {}

    def fake_post(url, params=None, json=None, timeout=None):
        captured.update(url=url, params=params, json=json, timeout=timeout)
        return FakeResponse(_text_payload('{"sentiment": 0.9, "reason": "ok"}'))

    monkeypatch.setattr(mod.httpx, "post", fake_post)
    out = GeminiClient(api_key="k", model="gemini-2.5-flash").generate_json("PROMPT")
    assert out == {"sentiment": 0.9, "reason": "ok"}
    assert captured["params"]["key"] == "k"
    assert "gemini-2.5-flash" in captured["url"]
    assert captured["json"]["contents"][0]["parts"][0]["text"] == "PROMPT"
    assert captured["json"]["generationConfig"]["responseMimeType"] == "application/json"
    assert captured["json"]["generationConfig"]["temperature"] == 0


def test_http_500_returns_none(monkeypatch, caplog):
    monkeypatch.setattr(mod.httpx, "post", lambda *a, **k: FakeResponse({}, status=500))
    with caplog.at_level(logging.WARNING):
        out = GeminiClient(api_key="k", max_attempts=1).generate_json("hello")
    assert out is None
    assert "gemini" in caplog.text.lower()


def test_http_403_returns_none_without_retry(monkeypatch):
    calls = {"n": 0}

    def fake_post(*a, **k):
        calls["n"] += 1
        return FakeResponse({}, status=403)

    monkeypatch.setattr(mod.httpx, "post", fake_post)
    out = GeminiClient(api_key="k", max_attempts=2).generate_json("hello")
    assert out is None
    assert calls["n"] == 1  # 4xx 不重试


def test_malformed_text_returns_none(monkeypatch, caplog):
    monkeypatch.setattr(mod.httpx, "post", lambda *a, **k: FakeResponse(_text_payload("not json")))
    with caplog.at_level(logging.WARNING):
        out = GeminiClient(api_key="k").generate_json("hello")
    assert out is None
    assert "gemini" in caplog.text.lower()


def test_missing_candidates_returns_none(monkeypatch, caplog):
    monkeypatch.setattr(mod.httpx, "post", lambda *a, **k: FakeResponse({}))
    with caplog.at_level(logging.WARNING):
        out = GeminiClient(api_key="k").generate_json("hello")
    assert out is None


def test_never_raises_on_unexpected_response_shape(monkeypatch):
    monkeypatch.setattr(mod.httpx, "post", lambda *a, **k: FakeResponse("not-a-dict"))
    out = GeminiClient(api_key="k").generate_json("hi")
    assert out is None


def test_transport_error_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def flaky_post(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom")
        return FakeResponse(_text_payload('{"sentiment": 0.1}'))

    monkeypatch.setattr(mod.httpx, "post", flaky_post)
    out = GeminiClient(api_key="k", max_attempts=2).generate_json("hi")
    assert out == {"sentiment": 0.1}
    assert calls["n"] == 2


def test_transport_error_all_attempts_fail_returns_none(monkeypatch, caplog):
    def always_fail(*a, **k):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(mod.httpx, "post", always_fail)
    with caplog.at_level(logging.WARNING):
        out = GeminiClient(api_key="k", max_attempts=2).generate_json("hi")
    assert out is None


def test_uses_settings_when_not_overridden(monkeypatch):
    class FakeSettings:
        gemini_api_key = "from-settings-not-real"
        gemini_model = "model-from-settings"

    monkeypatch.setattr(mod, "get_settings", lambda: FakeSettings())
    client = GeminiClient()
    assert client._api_key == "from-settings-not-real"
    assert client._model == "model-from-settings"
