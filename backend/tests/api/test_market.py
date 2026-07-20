"""GET /api/market/regime —— 只读:SPY 相对其 200 日均线的风险开关状态(市场背景参考)。

全离线(见 tests/conftest.py 的联网熔断):用本文件内的 fake provider 通过
app.dependency_overrides[get_provider] 注入固定的 SPY 序列/缺数据/抛错,不发起
任何真实网络请求。与 tests/api/test_marks.py 的只读断言风格一致。
"""
import datetime as dt

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_provider
from app.api.security import require_token
from app.data.base import PriceProvider, empty_bars
from app.main import app


class _SpyProvider(PriceProvider):
    """离线测试行情源:'rising'/'declining' 构造单调序列(避免均线临界值抖动);
    'empty' 模拟无 SPY 数据;'raise' 模拟 provider 整体故障。"""

    def __init__(self, mode: str):
        self._mode = mode

    def get_daily_bars(self, symbol: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        if self._mode == "raise":
            raise RuntimeError("provider unavailable")
        if self._mode == "empty" or symbol != "SPY" or start > end:
            return empty_bars()
        idx = pd.date_range(start, end, freq="D")
        n = len(idx)
        if self._mode == "rising":
            closes = 300.0 + 0.5 * np.arange(n)
        elif self._mode == "declining":
            closes = 500.0 - 0.5 * np.arange(n)
        else:
            raise ValueError(f"unknown mode {self._mode}")
        return pd.DataFrame(
            {"open": closes, "high": closes, "low": closes, "close": closes,
             "volume": 1_000_000.0},
            index=idx,
        )


def _client_with_provider(provider: PriceProvider) -> TestClient:
    app.dependency_overrides[get_provider] = lambda: provider
    app.dependency_overrides[require_token] = lambda: None
    return TestClient(app)


def test_market_regime_rising_spy_is_risk_on():
    client = _client_with_provider(_SpyProvider("rising"))
    with client:
        resp = client.get("/api/market/regime")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["risk_on"] is True
    assert body["spy_close"] is not None
    assert body["spy_sma200"] is not None
    assert body["distance_pct"] > 0
    assert body["spy_close"] > body["spy_sma200"]


def test_market_regime_declining_spy_is_risk_off():
    client = _client_with_provider(_SpyProvider("declining"))
    with client:
        resp = client.get("/api/market/regime")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["risk_on"] is False
    assert body["distance_pct"] < 0
    assert body["spy_close"] < body["spy_sma200"]


def test_market_regime_no_spy_data_degrades_to_unavailable():
    client = _client_with_provider(_SpyProvider("empty"))
    with client:
        resp = client.get("/api/market/regime")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["risk_on"] is None
    assert body["spy_close"] is None
    assert body["spy_sma200"] is None
    assert body["distance_pct"] is None
    assert "as_of" in body


def test_market_regime_provider_raising_degrades_gracefully():
    client = _client_with_provider(_SpyProvider("raise"))
    with client:
        resp = client.get("/api/market/regime")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["risk_on"] is None
    assert body["spy_close"] is None
    assert body["spy_sma200"] is None
    assert body["distance_pct"] is None


def test_market_regime_does_not_require_token():
    """GET 只读端点不设 token 门禁(与 marks/dashboard/history 一致):不覆盖
    require_token 也应该 200,证明这条路由压根没挂 require_token 依赖。"""
    app.dependency_overrides[get_provider] = lambda: _SpyProvider("empty")
    app.dependency_overrides.pop(require_token, None)
    with TestClient(app) as c:
        resp = c.get("/api/market/regime")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
