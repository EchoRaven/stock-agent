"""POST /api/reflect —— 平仓复盘(Phase 2)。token 门禁的专门覆盖(403 without
token / 200 with token,同 tests/api/test_security.py 的 unsecured_client 模式)
+ 业务薄壳(装配 session/gemini,commit,返回 count)。编排逻辑本身(均价法/
幂等/rationale/LLM 教训)已在 tests/services/test_reflection_service.py 覆盖。
"""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.api import security
from app.api.deps import get_gemini_client, get_provider, get_session
from app.api.security import current_token, require_token
from app.main import app
from app.store.repos.memory_repo import get_entries
from app.store.repos.paper_repo import add_fill
from tests.api.conftest import FakeProvider

D1 = dt.date(2026, 6, 1)
D2 = dt.date(2026, 6, 2)


def test_post_reflect_returns_zero_count_with_no_closed_trades(client):
    resp = client.post("/api/reflect")
    assert resp.status_code == 200
    assert resp.json() == {"reviews": [], "count": 0}


def test_post_reflect_writes_review_for_closed_trade(client, session):
    add_fill(session, 1, D1, "AAPL", "buy", 100, 10.0)
    add_fill(session, 2, D2, "AAPL", "sell", 100, 15.0)
    session.commit()

    resp = client.post("/api/reflect")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert len(body["reviews"]) == 1
    assert body["reviews"][0]["symbol"] == "AAPL"

    rows = get_entries(session, kind="trade_review")
    assert len(rows) == 1


def test_post_reflect_is_idempotent_over_http(client, session):
    add_fill(session, 1, D1, "AAPL", "buy", 100, 10.0)
    add_fill(session, 2, D2, "AAPL", "sell", 100, 15.0)
    session.commit()

    first = client.post("/api/reflect").json()
    second = client.post("/api/reflect").json()
    assert first["count"] == 1
    assert second["count"] == 0
    assert len(get_entries(session, kind="trade_review")) == 1


# ---------------------------------------------------------------------------
# token 门禁(状态变更路由,同 /api/memory POST)。
# ---------------------------------------------------------------------------


@pytest.fixture
def token_env(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKAGENT_DB_PATH", str(tmp_path / "stockagent.db"))
    security._TOKEN_CACHE.clear()
    yield
    security._TOKEN_CACHE.clear()


@pytest.fixture
def unsecured_client(session, token_env):
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_provider] = lambda: FakeProvider()
    app.dependency_overrides[get_gemini_client] = lambda: None
    app.dependency_overrides.pop(require_token, None)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_post_reflect_without_token_is_forbidden(unsecured_client, session):
    add_fill(session, 1, D1, "AAPL", "buy", 100, 10.0)
    add_fill(session, 2, D2, "AAPL", "sell", 100, 15.0)
    session.commit()

    resp = unsecured_client.post("/api/reflect")
    assert resp.status_code == 403
    assert get_entries(session, kind="trade_review") == []  # 无副作用


def test_post_reflect_with_correct_token_succeeds(unsecured_client, token_env):
    token = current_token()
    resp = unsecured_client.post("/api/reflect", headers={"X-Stock-Agent-Token": token})
    assert resp.status_code == 200
    assert resp.json() == {"reviews": [], "count": 0}
