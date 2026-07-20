"""POST /api/picks —— 委员会排序荐股列表:薄壳装配测试,注入 fake
price/news/fundamentals/gemini,全离线。编排逻辑本身(排序/单标的容错/
analysis-only)已在 tests/services/test_picks_service.py 覆盖,这里只测装配 +
token 门禁 + gemini 未配置的 400(同 tests/api/test_stock.py analyze 的既有
证据模式)。
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
from app.data.fundamentals_edgar import FundamentalsProvider, FundamentalsSummary
from app.data.news_finnhub import NewsProvider
from app.main import app
from app.store.models import DecisionRow, OrderRow
from app.store.repos.paper_repo import get_positions


class FakePicksPriceProvider(PriceProvider):
    """所有标的都返回相同的温和上涨日线——这里只关心端点装配是否走通(session/
    provider/news/fundamentals/gemini 的 DI + token 门禁),不关心量化排序的
    具体数值(同 test_stock.py 的 FakeStockPriceProvider 思路)。"""

    def get_daily_bars(self, symbol, start, end):
        if start > end:
            return empty_bars()
        idx = pd.date_range(start, end, freq="D")
        n = len(idx)
        closes = [100.0 + i * 0.05 for i in range(n)]
        return pd.DataFrame(
            {"open": closes, "high": [c + 1 for c in closes], "low": [c - 1 for c in closes],
             "close": closes, "volume": [1_000_000.0] * n},
            index=idx,
        )


class FakePicksNews(NewsProvider):
    def get_company_news(self, symbol, start, end):
        return []


class FakePicksFunds(FundamentalsProvider):
    def get_fundamentals(self, symbol):
        return FundamentalsSummary(symbol)


class FakePicksGemini:
    """始终裁决 hold——本文件只测端点装配,不测委员会/排序逻辑本身。"""

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
    app.dependency_overrides[get_provider] = lambda: (price if price is not None
                                                       else FakePicksPriceProvider())
    app.dependency_overrides[get_news_provider] = lambda: (news if news is not None
                                                            else FakePicksNews())
    app.dependency_overrides[get_fundamentals_provider] = lambda: (funds if funds is not None
                                                                    else FakePicksFunds())
    app.dependency_overrides[get_gemini_client] = lambda: (gemini if gemini is not None
                                                            else FakePicksGemini())


def _clear_overrides():
    app.dependency_overrides.pop(get_provider, None)
    app.dependency_overrides.pop(get_news_provider, None)
    app.dependency_overrides.pop(get_fundamentals_provider, None)
    app.dependency_overrides.pop(get_gemini_client, None)


# ---------------------------------------------------------------------------
# 校验 / gemini 未配置 400(这两条不需要真正的 token,client fixture 已绕过门禁)
# ---------------------------------------------------------------------------


def test_picks_n_out_of_bounds_returns_422(client):
    resp = client.post("/api/picks", json={"n": 0})
    assert resp.status_code == 422
    resp = client.post("/api/picks", json={"n": 16})
    assert resp.status_code == 422


def test_picks_rejects_unknown_fields(client):
    resp = client.post("/api/picks", json={"n": 3, "unexpected": True})
    assert resp.status_code == 422


def test_picks_without_gemini_configured_returns_400(client, monkeypatch):
    monkeypatch.setenv("STOCKAGENT_GEMINI_API_KEY", "")
    app.dependency_overrides[get_provider] = lambda: FakePicksPriceProvider()
    app.dependency_overrides[get_news_provider] = lambda: FakePicksNews()
    app.dependency_overrides[get_fundamentals_provider] = lambda: FakePicksFunds()
    app.dependency_overrides[get_gemini_client] = lambda: None
    try:
        resp = client.post("/api/picks", json={"n": 3})
    finally:
        _clear_overrides()
    assert resp.status_code == 400
    assert "detail" in resp.json()


# ---------------------------------------------------------------------------
# token 门禁 + 成功路径(分析only:no order/decision 副作用)
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
    app.dependency_overrides[get_provider] = lambda: FakePicksPriceProvider()
    app.dependency_overrides.pop(require_token, None)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_picks_without_token_is_forbidden(client_no_token):
    resp = client_no_token.post("/api/picks", json={"n": 2})
    assert resp.status_code == 403


def test_picks_with_token_returns_ranked_picks_and_creates_no_decision_or_order(
        client_no_token, session, token_env):
    token = current_token()
    _override()
    before_positions = dict(get_positions(session))
    before_orders = list(session.scalars(select(OrderRow)))
    before_decisions = list(session.scalars(select(DecisionRow)))
    try:
        resp = client_no_token.post("/api/picks", json={"n": 2},
                                    headers={"X-Stock-Agent-Token": token})
    finally:
        _clear_overrides()
    assert resp.status_code == 200
    body = resp.json()
    assert body["n"] == 2
    assert len(body["picks"]) == 2
    for i, p in enumerate(body["picks"], start=1):
        assert p["rank"] == i
        assert "quant_score" in p
        assert p["action"] in ("buy", "sell", "hold")
        assert "confidence" in p
        assert "chair_verdict" in p
        assert "held" in p
    assert body["errors"] == []
    assert body["gemini_calls"] == 2

    # 安全红线:纯分析——不落库、不生成任何 decision/order/持仓变化
    assert dict(get_positions(session)) == before_positions
    assert list(session.scalars(select(OrderRow))) == before_orders
    assert list(session.scalars(select(DecisionRow))) == before_decisions


def test_picks_default_n_is_eight(client_no_token, token_env):
    token = current_token()
    _override()
    try:
        resp = client_no_token.post("/api/picks", json={},
                                    headers={"X-Stock-Agent-Token": token})
    finally:
        _clear_overrides()
    assert resp.status_code == 200
    body = resp.json()
    assert body["n"] == 8
    assert len(body["picks"]) == 8
