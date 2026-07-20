"""POST /api/trade/cycle —— 薄壳装配测试:注入 fake providers + fake Gemini,全离线。
编排逻辑本身在 tests/services/test_trade_cycle_service.py 已覆盖,这里只测装配 +
token 门禁(该端点会触发外部计费调用 + 可能在 full_auto 下建单)。
"""
import datetime as dt

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.api import security
from app.api.deps import (get_fundamentals_provider, get_gemini_client, get_news_provider,
                          get_provider, get_session)
from app.api.security import current_token, require_token
from app.data.base import PriceProvider, empty_bars
from app.data.fundamentals_edgar import FundamentalsProvider, FundamentalsSummary
from app.data.news_finnhub import NewsProvider
from app.main import app
from app.store.repos.settings_repo import MODE_FULL_AUTO, set_mode


class FakePriceProvider(PriceProvider):
    def __init__(self, price: float = 100.0):
        self._price = price

    def get_daily_bars(self, symbol, start, end):
        if start > end:
            return empty_bars()
        idx = pd.date_range(start, end, freq="D")
        return pd.DataFrame(
            {"open": self._price, "high": self._price + 1, "low": self._price - 1,
             "close": self._price, "volume": 1_000_000.0}, index=idx)


class FakeNews(NewsProvider):
    def get_company_news(self, symbol, start, end):
        return []


class FakeFunds(FundamentalsProvider):
    def get_fundamentals(self, symbol):
        return FundamentalsSummary(symbol)


class FakeGemini:
    """始终裁决 hold——本文件只测端点装配,不测委员会/交易逻辑本身。"""

    def generate_json(self, prompt):
        return {
            "committee": {
                "technical": {"summary": "t"}, "fundamental": {"summary": "f"},
                "sentiment": {"summary": "s"}, "bear": {"summary": "b"},
            },
            "chair": {"verdict": "v", "bear_rebuttal": "r"},
            "action": "hold", "confidence": 0.6,
        }


def _override(gemini=None):
    app.dependency_overrides[get_news_provider] = lambda: FakeNews()
    app.dependency_overrides[get_fundamentals_provider] = lambda: FakeFunds()
    app.dependency_overrides[get_gemini_client] = lambda: (gemini if gemini is not None
                                                            else FakeGemini())


def _clear_overrides():
    app.dependency_overrides.pop(get_news_provider, None)
    app.dependency_overrides.pop(get_fundamentals_provider, None)
    app.dependency_overrides.pop(get_gemini_client, None)


def test_trade_cycle_returns_summary_with_decisions(client, session):
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    _override()
    try:
        resp = client.post("/api/trade/cycle",
                           json={"universe": ["AAPL"], "max_eval": 1, "settle": True})
    finally:
        _clear_overrides()
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "full_auto"
    assert len(body["decisions"]) == 1
    assert body["decisions"][0]["symbol"] == "AAPL"
    assert body["decisions"][0]["action"] == "hold"
    assert "fills" in body and "errors" in body


def test_trade_cycle_unknown_field_returns_422(client, session):
    resp = client.post("/api/trade/cycle", json={"bogus": 1})
    assert resp.status_code == 422


def test_trade_cycle_max_eval_out_of_bounds_returns_422(client, session):
    # 安全红线:max_eval 未加界会放大每标的一次的行情+新闻+Gemini 调用量
    resp = client.post("/api/trade/cycle", json={"max_eval": 100_000})
    assert resp.status_code == 422


def test_trade_cycle_universe_too_long_returns_422(client, session):
    # 安全红线:universe 未加界会放大筛选环节的行情抓取量(不受 max_eval 覆盖,
    # 那只管评估阶段的委员会/LLM 调用数)。覆盖 news/fundamentals/gemini 依赖,
    # 保证就算校验还没生效(RED 阶段)整条编排也不会真的发起网络请求。
    _override()
    try:
        resp = client.post("/api/trade/cycle",
                           json={"universe": [f"SYM{i}" for i in range(201)]})
    finally:
        _clear_overrides()
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# token 门禁(该端点可能在 semi/full_auto 下建单,必须 CSRF token 门禁)。
# ---------------------------------------------------------------------------


@pytest.fixture
def token_env(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKAGENT_DB_PATH", str(tmp_path / "stockagent.db"))
    security._TOKEN_CACHE.clear()
    yield
    security._TOKEN_CACHE.clear()


@pytest.fixture
def unsecured_client(token_env, session):
    """不覆盖 require_token 的 client——唯一能证明门禁真正拦截的方式。其余依赖
    (session/provider/news/fundamentals/gemini)全部注入 fake,保证离线。"""
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_provider] = lambda: FakePriceProvider()
    _override()
    app.dependency_overrides.pop(require_token, None)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_trade_cycle_without_token_is_forbidden(unsecured_client):
    resp = unsecured_client.post("/api/trade/cycle",
                                 json={"universe": ["AAPL"], "max_eval": 1})
    assert resp.status_code == 403


def test_trade_cycle_with_correct_token_succeeds(unsecured_client, session, token_env):
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    token = current_token()
    resp = unsecured_client.post(
        "/api/trade/cycle", json={"universe": ["AAPL"], "max_eval": 1},
        headers={"X-Stock-Agent-Token": token})
    assert resp.status_code == 200
    assert resp.json()["mode"] == "full_auto"
