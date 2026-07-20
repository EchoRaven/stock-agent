"""GET /api/stock/{symbol} + POST /api/stock/{symbol}/analyze —— 薄壳装配测试:
注入 fake price/news/fundamentals/gemini,全离线。

GET 端点无 token 门禁(只读,不触发付费调用之外的行情/新闻/财报抓取——这些是
只读展示数据,与 /api/sentiment /api/trade/cycle 那类会花钱的 LLM 调用不同)。
POST /analyze 会触发一次 Gemini 调用,必须 token 门禁,且是"分析only"——绝不
调用 submit_decision / 建任何订单,这里用 get_positions 前后不变 + 无新订单
两条证据证明。
"""
import datetime as dt

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import security
from app.api.deps import (get_fundamentals_provider, get_gemini_client, get_news_provider,
                          get_provider, get_session)
from app.api.security import current_token, require_token
from app.data.base import PriceProvider, empty_bars
from app.data.fundamentals_edgar import FundamentalPoint, FundamentalsProvider, FundamentalsSummary
from app.data.news_finnhub import NewsItem, NewsProvider
from app.main import app
from app.store.models import DecisionRow, OrderRow
from app.store.repos.paper_repo import get_positions


class FakeStockPriceProvider(PriceProvider):
    """60 根日线,最后一根收盘价明显偏离,方便断言 last_close/52w high-low。"""

    def __init__(self, base: float = 100.0, empty: bool = False):
        self._base = base
        self._empty = empty

    def get_daily_bars(self, symbol, start, end):
        if self._empty or start > end:
            return empty_bars()
        idx = pd.date_range(start, end, freq="D")
        n = len(idx)
        closes = [self._base + i * 0.1 for i in range(n)]
        return pd.DataFrame(
            {"open": closes, "high": [c + 1 for c in closes], "low": [c - 1 for c in closes],
             "close": closes, "volume": [1_000_000.0] * n},
            index=idx,
        )


class FakeStockNews(NewsProvider):
    def __init__(self, items=None, fail=False):
        self._fail = fail
        self._items = items if items is not None else [
            NewsItem(published_at=dt.date(2026, 1, 4), headline="<b>AAPL beats</b> estimates",
                    summary="strong <i>quarter</i>", source="Reuters",
                    url="https://example.com/1"),
        ]

    def get_company_news(self, symbol, start, end):
        if self._fail:
            raise RuntimeError("news provider down")
        return self._items


class FakeStockFunds(FundamentalsProvider):
    def __init__(self, fail=False):
        self._fail = fail

    def get_fundamentals(self, symbol):
        if self._fail:
            raise RuntimeError("edgar down")
        point = FundamentalPoint(end=dt.date(2025, 12, 31), value=1000.0, fiscal="Q4-2025")
        return FundamentalsSummary(symbol, revenue=(point,), net_income=(point,), eps=(point,))


class FakeGemini:
    def generate_json(self, prompt):
        return {
            "committee": {
                "technical": {"summary": "t"}, "fundamental": {"summary": "f"},
                "sentiment": {"summary": "s"}, "bear": {"summary": "b"},
            },
            "chair": {"verdict": "v", "bear_rebuttal": "r"},
            "action": "hold", "confidence": 0.6,
        }


def _override(price=None, news=None, funds=None, gemini=None):
    if price is not None:
        app.dependency_overrides[get_provider] = lambda: price
    app.dependency_overrides[get_news_provider] = lambda: (news if news is not None
                                                            else FakeStockNews())
    app.dependency_overrides[get_fundamentals_provider] = lambda: (funds if funds is not None
                                                                    else FakeStockFunds())
    app.dependency_overrides[get_gemini_client] = lambda: (gemini if gemini is not None
                                                            else FakeGemini())


def _clear_overrides():
    app.dependency_overrides.pop(get_provider, None)
    app.dependency_overrides.pop(get_news_provider, None)
    app.dependency_overrides.pop(get_fundamentals_provider, None)
    app.dependency_overrides.pop(get_gemini_client, None)


# ---------------------------------------------------------------------------
# GET /api/stock/{symbol}
# ---------------------------------------------------------------------------


def test_get_stock_returns_price_series_summary_news_fundamentals(client):
    _override(price=FakeStockPriceProvider())
    try:
        resp = client.get("/api/stock/AAPL")
    finally:
        _clear_overrides()
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "AAPL"
    assert "as_of" in body and "days" in body

    series = body["price_series"]
    assert len(series) > 0
    for row in series:
        assert "date" in row and "close" in row

    summary = body["summary"]
    assert summary["last_close"] is not None
    assert summary["high_52w"] is not None
    assert summary["low_52w"] is not None
    assert summary["high_52w"] >= summary["low_52w"]

    news = body["news"]
    assert len(news) == 1
    # sanitize: HTML tags stripped from display fields
    assert "<b>" not in news[0]["headline"]
    assert "<i>" not in news[0]["summary"]
    assert "AAPL beats estimates" in news[0]["headline"]

    funds = body["fundamentals"]
    assert len(funds["revenue"]) == 1
    assert funds["revenue"][0]["value"] == 1000.0
    assert funds["revenue"][0]["fiscal"] == "Q4-2025"


def test_get_stock_empty_bars_returns_404(client):
    _override(price=FakeStockPriceProvider(empty=True))
    try:
        resp = client.get("/api/stock/NOPE")
    finally:
        _clear_overrides()
    assert resp.status_code == 404


def test_get_stock_days_too_small_returns_422(client):
    _override(price=FakeStockPriceProvider())
    try:
        resp = client.get("/api/stock/AAPL", params={"days": 5})
    finally:
        _clear_overrides()
    assert resp.status_code == 422


def test_get_stock_days_too_large_returns_422(client):
    _override(price=FakeStockPriceProvider())
    try:
        resp = client.get("/api/stock/AAPL", params={"days": 5000})
    finally:
        _clear_overrides()
    assert resp.status_code == 422


def test_get_stock_news_failure_does_not_500(client):
    """一个外部数据源(新闻)失败不该拖垮整页——价格/财报仍正常返回。"""
    _override(price=FakeStockPriceProvider(), news=FakeStockNews(fail=True))
    try:
        resp = client.get("/api/stock/AAPL")
    finally:
        _clear_overrides()
    assert resp.status_code == 200
    body = resp.json()
    assert body["news"] == []
    assert body["price_series"]
    assert len(body["fundamentals"]["revenue"]) == 1


def test_get_stock_fundamentals_failure_does_not_500(client):
    """财报源失败同样不该拖垮整页——价格/新闻仍正常返回。"""
    _override(price=FakeStockPriceProvider(), funds=FakeStockFunds(fail=True))
    try:
        resp = client.get("/api/stock/AAPL")
    finally:
        _clear_overrides()
    assert resp.status_code == 200
    body = resp.json()
    assert body["fundamentals"] == {} or body["fundamentals"] == {
        "revenue": [], "net_income": [], "eps": []}
    assert body["price_series"]
    assert len(body["news"]) == 1


# ---------------------------------------------------------------------------
# POST /api/stock/{symbol}/analyze —— 分析only,绝不下单
# ---------------------------------------------------------------------------


def test_analyze_without_token_is_forbidden(client_no_token):
    resp = client_no_token.post("/api/stock/AAPL/analyze")
    assert resp.status_code == 403


def test_analyze_with_token_returns_committee_and_no_order_created(
        client_no_token, session, token_env):
    token = current_token()
    _override(price=FakeStockPriceProvider())
    before_positions = dict(get_positions(session))
    before_orders = list(session.scalars(select(OrderRow)))
    before_decisions = list(session.scalars(select(DecisionRow)))
    try:
        resp = client_no_token.post("/api/stock/AAPL/analyze",
                                    headers={"X-Stock-Agent-Token": token})
    finally:
        _clear_overrides()
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "AAPL"
    assert set(body["committee"].keys()) == {"technical", "fundamental", "sentiment", "bear"}
    assert body["chair"]["verdict"] == "v"
    assert body["chair"]["bear_rebuttal"] == "r"
    assert body["action"] in ("buy", "sell", "hold")
    assert "confidence" in body
    assert "note" in body and "no order" in body["note"].lower()

    # analysis-only: no order/decision/position side effects whatsoever
    assert dict(get_positions(session)) == before_positions
    assert list(session.scalars(select(OrderRow))) == before_orders
    assert list(session.scalars(select(DecisionRow))) == before_decisions


def test_analyze_without_gemini_configured_returns_400(client, monkeypatch):
    monkeypatch.setenv("STOCKAGENT_GEMINI_API_KEY", "")
    app.dependency_overrides[get_provider] = lambda: FakeStockPriceProvider()
    app.dependency_overrides[get_news_provider] = lambda: FakeStockNews()
    app.dependency_overrides[get_fundamentals_provider] = lambda: FakeStockFunds()
    app.dependency_overrides[get_gemini_client] = lambda: None
    try:
        resp = client.post("/api/stock/AAPL/analyze")
    finally:
        _clear_overrides()
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# fixtures for the token-gated tests (mirrors tests/api/test_security.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def token_env(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKAGENT_DB_PATH", str(tmp_path / "stockagent.db"))
    security._TOKEN_CACHE.clear()
    yield
    security._TOKEN_CACHE.clear()


@pytest.fixture
def client_no_token(session, token_env):
    """不覆盖 require_token 的 client——唯一能证明门禁真正拦截的方式。"""
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_provider] = lambda: FakeStockPriceProvider()
    app.dependency_overrides.pop(require_token, None)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
