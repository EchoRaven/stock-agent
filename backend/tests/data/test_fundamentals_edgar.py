import datetime as dt
import logging

import httpx
import pytest

import app.data.fundamentals_edgar as mod
from app.data.fundamentals_edgar import (EdgarFundamentalsProvider, FundamentalPoint,
                                         FundamentalsSummary)


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)

    def json(self):
        return self._payload


TICKERS = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}

FACTS = {
    "facts": {
        "us-gaap": {
            "Revenues": {"units": {"USD": [
                {"end": "2025-12-31", "val": 100.0, "fy": 2025, "fp": "FY", "form": "10-K"},
                {"end": "2026-03-31", "val": 30.0, "fy": 2026, "fp": "Q1", "form": "10-Q"},
                {"end": "2026-03-31", "val": 31.0, "fy": 2026, "fp": "Q1", "form": "10-Q"},
                {"end": "2020-12-31", "val": 1.0, "fy": 2020, "fp": "FY", "form": "8-K"},
            ]}},
            "NetIncomeLoss": {"units": {"USD": [
                {"end": "2026-03-31", "val": 10.0, "fy": 2026, "fp": "Q1", "form": "10-Q"},
            ]}},
            "EarningsPerShareDiluted": {"units": {"USD/shares": [
                {"end": "2026-03-31", "val": 1.5, "fy": 2026, "fp": "Q1", "form": "10-Q"},
            ]}},
        }
    }
}


def _router(captured=None):
    def fake_get(url, headers=None, timeout=None):
        if captured is not None:
            captured.append((url, headers))
        if url == mod.TICKERS_URL:
            return FakeResponse(TICKERS)
        return FakeResponse(FACTS)

    return fake_get


def test_requires_user_agent():
    with pytest.raises(ValueError):
        EdgarFundamentalsProvider(user_agent="   ")


def test_summary_extraction(monkeypatch):
    captured = []
    monkeypatch.setattr(mod.httpx, "get", _router(captured))
    out = EdgarFundamentalsProvider(user_agent="ua test").get_fundamentals("aapl")
    assert isinstance(out, FundamentalsSummary) and out.symbol == "AAPL"
    # 新→旧;同 end 修正值(31.0)覆盖;8-K 被忽略
    assert [p.value for p in out.revenue] == [31.0, 100.0]
    assert out.revenue[0] == FundamentalPoint(dt.date(2026, 3, 31), 31.0, "Q1-2026")
    assert out.net_income[0].value == 10.0
    assert out.eps[0].value == 1.5
    assert all(h["User-Agent"] == "ua test" for _, h in captured)
    assert captured[1][0] == mod.FACTS_URL.format(cik=320193)


def test_unknown_ticker_returns_empty(monkeypatch, caplog):
    monkeypatch.setattr(mod.httpx, "get", _router())
    with caplog.at_level(logging.WARNING):
        out = EdgarFundamentalsProvider(user_agent="ua").get_fundamentals("ZZZZ")
    assert out == FundamentalsSummary("ZZZZ")
    assert "CIK" in caplog.text


def test_http_error_returns_empty(monkeypatch):
    monkeypatch.setattr(mod.httpx, "get", lambda *a, **k: FakeResponse({}, status=503))
    out = EdgarFundamentalsProvider(user_agent="ua").get_fundamentals("AAPL")
    assert out.revenue == () and out.net_income == () and out.eps == ()


def test_fiscal_field_is_sanitized(monkeypatch):
    """EDGAR fp/fy 原样拼进 fiscal 前必须先 sanitize_text(HTML 剥 + 截断),
    因为它来自远端 JSON,并非受控常量。"""
    overlong_fp = "<b>Q1</b>" + "x" * 30
    facts = {
        "facts": {
            "us-gaap": {
                "Revenues": {"units": {"USD": [
                    {"end": "2026-03-31", "val": 30.0, "fy": 2026, "fp": overlong_fp,
                     "form": "10-Q"},
                ]}},
            }
        }
    }

    def fake_get(url, headers=None, timeout=None):
        if url == mod.TICKERS_URL:
            return FakeResponse(TICKERS)
        return FakeResponse(facts)

    monkeypatch.setattr(mod.httpx, "get", fake_get)
    out = EdgarFundamentalsProvider(user_agent="ua").get_fundamentals("AAPL")
    fiscal = out.revenue[0].fiscal
    assert "<b>" not in fiscal and "</b>" not in fiscal
    assert len(fiscal) <= 20


@pytest.mark.network
def test_edgar_real_fetch():
    """真实联网:pytest -m network 手动运行(UA 需带联系方式)。"""
    p = EdgarFundamentalsProvider(user_agent="stock-agent test tonghaibo020@gmail.com")
    out = p.get_fundamentals("AAPL")
    assert out.revenue or out.net_income
