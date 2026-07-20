"""GET/POST/DELETE /api/watchlist —— 持久自选股清单 + 实时报价。

全离线(见 tests/conftest.py 的联网熔断):用本文件内的 fake provider 通过
app.dependency_overrides[get_provider] 注入固定/局部缺失/抛错的行情,不发起
任何真实网络请求。GET 只读不设 token 门禁(与 marks/dashboard 一致,风格见
tests/api/test_marks.py);POST/DELETE 是状态变更,门禁覆盖测试风格与
tests/api/test_security.py 的 unsecured_client 模式一致(本仓库第一个 DELETE
端点)。
"""
import datetime as dt

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.api import security
from app.api.deps import get_provider, get_session
from app.api.security import current_token, require_token
from app.data.base import PriceProvider, empty_bars
from app.main import app
from app.store.repos.watchlist_repo import add as repo_add


class _SeriesPriceProvider(PriceProvider):
    """离线测试行情源:每标的可配一段升序收盘价序列(最近若干日,最后一个是
    "今天");不在 series 里的标的返回空 bars;raise_error=True 模拟 provider
    整体故障。"""

    def __init__(self, series: dict | None = None, raise_error: bool = False):
        self._series = series or {}
        self._raise_error = raise_error

    def get_daily_bars(self, symbol: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        if self._raise_error:
            raise RuntimeError("provider unavailable")
        closes = self._series.get(symbol)
        if not closes or start > end:
            return empty_bars()
        idx = pd.date_range(end=end, periods=len(closes), freq="D")
        return pd.DataFrame(
            {"open": closes, "high": closes, "low": closes, "close": closes,
             "volume": [1_000_000.0] * len(closes)},
            index=idx,
        )


def _client_with_provider(session, provider: PriceProvider) -> TestClient:
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_provider] = lambda: provider
    app.dependency_overrides[require_token] = lambda: None
    return TestClient(app)


# ---- GET (read-only) ----

def test_get_empty_watchlist_returns_empty_list(session):
    client = _client_with_provider(session, _SeriesPriceProvider({}))
    with client:
        resp = client.get("/api/watchlist")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json() == []


def test_get_computes_current_prev_change_for_watched_symbols(session):
    repo_add(session, "AAA", note="watching")
    repo_add(session, "BBB")
    session.commit()

    provider = _SeriesPriceProvider({"AAA": [100.0, 110.0], "BBB": [50.0, 45.0]})
    client = _client_with_provider(session, provider)
    with client:
        resp = client.get("/api/watchlist")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    by_symbol = {row["symbol"]: row for row in resp.json()}

    aaa = by_symbol["AAA"]
    assert aaa["note"] == "watching"
    assert aaa["current_price"] == pytest.approx(110.0)
    assert aaa["prev_close"] == pytest.approx(100.0)
    assert aaa["change"] == pytest.approx(10.0)
    assert aaa["change_pct"] == pytest.approx(10.0)

    bbb = by_symbol["BBB"]
    assert bbb["current_price"] == pytest.approx(45.0)
    assert bbb["prev_close"] == pytest.approx(50.0)
    assert bbb["change"] == pytest.approx(-5.0)
    assert bbb["change_pct"] == pytest.approx(-10.0)


def test_get_symbol_with_no_bars_has_none_price_fields(session):
    repo_add(session, "ZZZ")
    session.commit()

    client = _client_with_provider(session, _SeriesPriceProvider({}))
    with client:
        resp = client.get("/api/watchlist")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    row = resp.json()[0]
    assert row["symbol"] == "ZZZ"
    assert row["current_price"] is None
    assert row["prev_close"] is None
    assert row["change"] is None
    assert row["change_pct"] is None


def test_get_provider_raising_degrades_to_all_none_prices(session):
    repo_add(session, "AAA")
    repo_add(session, "BBB")
    session.commit()

    client = _client_with_provider(session, _SeriesPriceProvider(raise_error=True))
    with client:
        resp = client.get("/api/watchlist")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2
    for row in rows:
        assert row["current_price"] is None
        assert row["prev_close"] is None
        assert row["change"] is None
        assert row["change_pct"] is None


def test_get_does_not_require_token(session):
    """GET 只读端点不设 token 门禁:不覆盖 require_token 也应该 200,证明这条
    路由压根没挂 require_token 依赖。"""
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_provider] = lambda: _SeriesPriceProvider({})
    app.dependency_overrides.pop(require_token, None)
    with TestClient(app) as c:
        resp = c.get("/api/watchlist")
    app.dependency_overrides.clear()

    assert resp.status_code == 200


# ---- token gate (POST/DELETE) ----

@pytest.fixture
def token_env(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKAGENT_DB_PATH", str(tmp_path / "stockagent.db"))
    security._TOKEN_CACHE.clear()
    yield
    security._TOKEN_CACHE.clear()


@pytest.fixture
def unsecured_client(session, token_env):
    """不覆盖 require_token 的 client——唯一能证明门禁真正拦截的方式。"""
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_provider] = lambda: _SeriesPriceProvider({})
    app.dependency_overrides.pop(require_token, None)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ---- POST ----

def test_post_without_token_is_forbidden(unsecured_client, session):
    resp = unsecured_client.post("/api/watchlist", json={"symbol": "AAPL"})
    assert resp.status_code == 403
    assert unsecured_client.get("/api/watchlist").json() == []  # 无副作用


def test_post_with_token_adds_and_returns_row(unsecured_client, token_env, session):
    token = current_token()
    resp = unsecured_client.post("/api/watchlist", json={"symbol": "aapl", "note": "core"},
                                 headers={"X-Stock-Agent-Token": token})
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "AAPL"
    assert body["note"] == "core"
    assert "added_at" in body


def test_post_empty_symbol_is_rejected(unsecured_client, token_env):
    token = current_token()
    resp = unsecured_client.post("/api/watchlist", json={"symbol": ""},
                                 headers={"X-Stock-Agent-Token": token})
    assert resp.status_code in (400, 422)


def test_post_whitespace_symbol_is_rejected(unsecured_client, token_env):
    token = current_token()
    resp = unsecured_client.post("/api/watchlist", json={"symbol": "   "},
                                 headers={"X-Stock-Agent-Token": token})
    assert resp.status_code in (400, 422)


def test_post_unknown_field_is_422(unsecured_client, token_env):
    token = current_token()
    resp = unsecured_client.post("/api/watchlist", json={"symbol": "AAPL", "bogus": 1},
                                 headers={"X-Stock-Agent-Token": token})
    assert resp.status_code == 422


def test_post_existing_symbol_upserts_no_duplicate(unsecured_client, token_env, session):
    token = current_token()
    headers = {"X-Stock-Agent-Token": token}
    resp1 = unsecured_client.post("/api/watchlist", json={"symbol": "AAPL", "note": "v1"},
                                  headers=headers)
    assert resp1.status_code == 200
    resp2 = unsecured_client.post("/api/watchlist", json={"symbol": "aapl", "note": "v2"},
                                  headers=headers)
    assert resp2.status_code == 200
    assert resp2.json()["note"] == "v2"

    listing = unsecured_client.get("/api/watchlist").json()
    assert len(listing) == 1
    assert listing[0]["note"] == "v2"


# ---- DELETE ----

def test_delete_without_token_is_forbidden(unsecured_client, session):
    repo_add(session, "AAPL")
    session.commit()

    resp = unsecured_client.delete("/api/watchlist/AAPL")
    assert resp.status_code == 403
    listing = unsecured_client.get("/api/watchlist").json()
    assert [r["symbol"] for r in listing] == ["AAPL"]  # 无副作用


def test_delete_with_token_removes_row(unsecured_client, token_env, session):
    repo_add(session, "AAPL")
    session.commit()
    token = current_token()

    resp = unsecured_client.delete("/api/watchlist/aapl",
                                   headers={"X-Stock-Agent-Token": token})
    assert resp.status_code == 200
    assert resp.json() == {"removed": True, "symbol": "AAPL"}
    assert unsecured_client.get("/api/watchlist").json() == []


def test_delete_absent_symbol_returns_removed_false(unsecured_client, token_env, session):
    token = current_token()
    resp = unsecured_client.delete("/api/watchlist/NOPE",
                                   headers={"X-Stock-Agent-Token": token})
    assert resp.status_code == 200
    assert resp.json() == {"removed": False, "symbol": "NOPE"}
