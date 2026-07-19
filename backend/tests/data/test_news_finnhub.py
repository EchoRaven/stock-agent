import datetime as dt
import logging

import httpx
import pytest

import app.data.news_finnhub as mod
from app.data.news_finnhub import FinnhubNewsProvider, NewsItem

START, END = dt.date(2026, 7, 10), dt.date(2026, 7, 17)


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=self)

    def json(self):
        return self._payload


def test_no_key_returns_empty_with_warning(caplog):
    with caplog.at_level(logging.WARNING):
        out = FinnhubNewsProvider(api_key="").get_company_news("AAPL", START, END)
    assert out == []
    assert "finnhub" in caplog.text.lower()


def test_parses_items_sorted_desc(monkeypatch):
    payload = [
        {"datetime": 1760659200, "headline": "old", "summary": "s1", "source": "a", "url": "u1"},
        {"datetime": 1760832000, "headline": "new", "summary": "s2", "source": "b", "url": "u2"},
    ]
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured.update(url=url, params=params)
        return FakeResponse(payload)

    monkeypatch.setattr(mod.httpx, "get", fake_get)
    out = FinnhubNewsProvider(api_key="k").get_company_news("aapl", START, END)
    assert [n.headline for n in out] == ["new", "old"]
    assert isinstance(out[0], NewsItem) and isinstance(out[0].published_at, dt.date)
    assert captured["url"] == mod.COMPANY_NEWS_URL
    assert captured["params"]["symbol"] == "AAPL"
    assert captured["params"]["from"] == "2026-07-10"
    assert captured["params"]["token"] == "k"


def test_http_error_returns_empty(monkeypatch, caplog):
    monkeypatch.setattr(mod.httpx, "get", lambda *a, **k: FakeResponse([], status=500))
    with caplog.at_level(logging.WARNING):
        out = FinnhubNewsProvider(api_key="k").get_company_news("AAPL", START, END)
    assert out == []
    assert "finnhub" in caplog.text.lower()


def test_max_items_cap(monkeypatch):
    payload = [{"datetime": 1760659200 + i, "headline": f"h{i}", "summary": "", "source": "", "url": ""}
               for i in range(30)]
    monkeypatch.setattr(mod.httpx, "get", lambda *a, **k: FakeResponse(payload))
    out = FinnhubNewsProvider(api_key="k", max_items=5).get_company_news("AAPL", START, END)
    assert len(out) == 5


def test_http_status_error_never_leaks_key_in_log(monkeypatch, caplog):
    """403 响应触发 raise_for_status();日志必须只含状态码,绝不含 token。"""
    request = httpx.Request(
        "GET", "https://finnhub.io/api/v1/company-news",
        params={"symbol": "AAPL", "token": "SECRETKEY123"},
    )
    response = httpx.Response(403, request=request)

    monkeypatch.setattr(mod.httpx, "get", lambda *a, **k: response)
    with caplog.at_level(logging.WARNING):
        out = FinnhubNewsProvider(api_key="SECRETKEY123").get_company_news("AAPL", START, END)
    assert out == []
    assert "SECRETKEY123" not in caplog.text
    assert "403" in caplog.text


def test_transport_error_logs_type_name_only(monkeypatch, caplog):
    """连接类异常(非 HTTP 状态码)只记录异常类型名,不泄露任何请求细节/key。"""
    def fake_get(url, params=None, timeout=None):
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(mod.httpx, "get", fake_get)
    with caplog.at_level(logging.WARNING):
        out = FinnhubNewsProvider(api_key="SECRETKEY123").get_company_news("AAPL", START, END)
    assert out == []
    assert "ConnectError" in caplog.text
    assert "SECRETKEY123" not in caplog.text


@pytest.mark.network
def test_finnhub_real_fetch():
    """真实联网:需要 STOCKAGENT_FINNHUB_API_KEY,pytest -m network 手动运行。"""
    import os

    key = os.environ.get("STOCKAGENT_FINNHUB_API_KEY", "")
    if not key:
        pytest.skip("no finnhub key configured")
    out = FinnhubNewsProvider(api_key=key).get_company_news(
        "AAPL", dt.date.today() - dt.timedelta(days=7), dt.date.today())
    assert isinstance(out, list)
