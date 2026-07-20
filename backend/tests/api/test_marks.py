"""GET /api/positions/marks —— 只读:按最新收盘价盯市重估持仓的未实现盈亏。

全离线(见 tests/conftest.py 的联网熔断):用本文件内的 fake provider 通过
app.dependency_overrides[get_provider] 注入固定/局部缺失/抛错的行情,不发起
任何真实网络请求。与 tests/api/test_dashboard.py 的只读断言风格一致。
"""
import datetime as dt

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_provider, get_session
from app.api.security import require_token
from app.data.base import PriceProvider, empty_bars
from app.main import app
from app.store.repos.paper_repo import set_position


class _MultiPriceProvider(PriceProvider):
    """离线测试行情源:每标的可配不同收盘价;不在 prices 里的标的返回空 bars
    (模拟该标的取不到价);raise_error=True 模拟 provider 整体故障。"""

    def __init__(self, prices: dict | None = None, raise_error: bool = False):
        self._prices = prices or {}
        self._raise_error = raise_error

    def get_daily_bars(self, symbol: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        if self._raise_error:
            raise RuntimeError("provider unavailable")
        if symbol not in self._prices or start > end:
            return empty_bars()
        price = self._prices[symbol]
        idx = pd.date_range(start, end, freq="D")
        return pd.DataFrame(
            {"open": price, "high": price, "low": price, "close": price,
             "volume": 1_000_000.0},
            index=idx,
        )


def _client_with_provider(session, provider: PriceProvider) -> TestClient:
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_provider] = lambda: provider
    app.dependency_overrides[require_token] = lambda: None
    return TestClient(app)


def test_marks_priced_positions_computes_unrealized_and_totals(session):
    set_position(session, "AAA", 10, 100.0)
    set_position(session, "BBB", 5, 50.0)
    session.commit()

    client = _client_with_provider(session, _MultiPriceProvider({"AAA": 120.0, "BBB": 40.0}))
    with client:
        resp = client.get("/api/positions/marks")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    by_symbol = {p["symbol"]: p for p in body["positions"]}

    aaa = by_symbol["AAA"]
    assert aaa["cost_basis"] == pytest.approx(1000.0)
    assert aaa["current_price"] == pytest.approx(120.0)
    assert aaa["market_value"] == pytest.approx(1200.0)
    assert aaa["unrealized"] == pytest.approx(200.0)
    assert aaa["unrealized_pct"] == pytest.approx(20.0)

    bbb = by_symbol["BBB"]
    assert bbb["cost_basis"] == pytest.approx(250.0)
    assert bbb["current_price"] == pytest.approx(40.0)
    assert bbb["market_value"] == pytest.approx(200.0)
    assert bbb["unrealized"] == pytest.approx(-50.0)
    assert bbb["unrealized_pct"] == pytest.approx(-20.0)

    assert body["priced"] == 2
    assert body["unpriced"] == []
    assert body["total_cost"] == pytest.approx(1250.0)
    assert body["total_market_value"] == pytest.approx(1400.0)
    assert body["total_unrealized"] == pytest.approx(150.0)
    assert body["total_unrealized_pct"] == pytest.approx(12.0)
    assert body["cash"] == pytest.approx(100_000.0)
    assert body["equity"] == pytest.approx(100_000.0 + 1400.0)


def test_marks_unpriced_symbol_excluded_from_totals_but_counted_in_equity(session):
    set_position(session, "AAA", 10, 100.0)
    set_position(session, "ZZZ", 3, 30.0)  # 无法取价的标的
    session.commit()

    client = _client_with_provider(session, _MultiPriceProvider({"AAA": 120.0}))
    with client:
        resp = client.get("/api/positions/marks")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    by_symbol = {p["symbol"]: p for p in body["positions"]}

    zzz = by_symbol["ZZZ"]
    assert zzz["current_price"] is None
    assert zzz["market_value"] is None
    assert zzz["unrealized"] is None
    assert zzz["unrealized_pct"] is None

    assert body["unpriced"] == ["ZZZ"]
    assert body["priced"] == 1
    # 只统计已定价的持仓
    assert body["total_cost"] == pytest.approx(1000.0)
    assert body["total_market_value"] == pytest.approx(1200.0)
    assert body["total_unrealized"] == pytest.approx(200.0)
    # equity 里未定价持仓退回用 cost 近似,不因取价失败漏掉这块仓位价值
    assert body["equity"] == pytest.approx(100_000.0 + 1200.0 + 90.0)


def test_marks_empty_positions_returns_zeros(session):
    client = _client_with_provider(session, _MultiPriceProvider({}))
    with client:
        resp = client.get("/api/positions/marks")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["positions"] == []
    assert body["priced"] == 0
    assert body["unpriced"] == []
    assert body["total_cost"] == pytest.approx(0.0)
    assert body["total_market_value"] == pytest.approx(0.0)
    assert body["total_unrealized"] == pytest.approx(0.0)
    assert body["total_unrealized_pct"] is None
    assert body["cash"] == pytest.approx(100_000.0)
    assert body["equity"] == pytest.approx(100_000.0)


def test_marks_provider_raising_degrades_to_all_unpriced(session):
    set_position(session, "AAA", 10, 100.0)
    set_position(session, "BBB", 5, 50.0)
    session.commit()

    client = _client_with_provider(session, _MultiPriceProvider(raise_error=True))
    with client:
        resp = client.get("/api/positions/marks")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert sorted(body["unpriced"]) == ["AAA", "BBB"]
    assert body["priced"] == 0
    assert body["total_market_value"] == pytest.approx(0.0)
    assert body["total_unrealized"] == pytest.approx(0.0)
    assert body["total_unrealized_pct"] is None
    for p in body["positions"]:
        assert p["current_price"] is None
        assert p["market_value"] is None
        assert p["unrealized"] is None
    # 仍然拿 cost 近似把仓位价值算进 equity
    assert body["equity"] == pytest.approx(100_000.0 + 1000.0 + 250.0)


def test_marks_read_only_does_not_mutate_cash(session):
    set_position(session, "AAA", 10, 100.0)
    session.commit()

    client = _client_with_provider(session, _MultiPriceProvider({"AAA": 999.0}))
    with client:
        resp = client.get("/api/positions/marks")
        assert resp.status_code == 200

        from app.store.repos.paper_repo import get_account
        account = get_account(session, 100_000.0)
        assert account.cash == pytest.approx(100_000.0)
    app.dependency_overrides.clear()


def test_marks_does_not_require_token(session):
    """GET 只读端点不设 token 门禁(与 dashboard/history 一致):不覆盖
    require_token 也应该 200,证明这条路由压根没挂 require_token 依赖。"""
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_provider] = lambda: _MultiPriceProvider({})
    app.dependency_overrides.pop(require_token, None)
    with TestClient(app) as c:
        resp = c.get("/api/positions/marks")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
