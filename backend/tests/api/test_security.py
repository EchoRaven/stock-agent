"""安全红线覆盖:状态变更(POST)路由必须要求正确的 X-Stock-Agent-Token。

approve/reject 不带请求体,浏览器把它们当 CORS "simple request"——不触发预检,
main.py 的 Origin 白名单不会被咨询。任意跨站页面都能悄悄 POST 过来,绕过
semi_auto 的人工确认闸门(见 app/api/security.py 顶部说明)。这里用一个 **不**
覆盖 require_token 的 client 证明门禁真正生效;tests/api/conftest.py 里共享的
`client` fixture 覆盖了 require_token(no-op),所以其余 22 个已有测试不受影响。
"""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.api import security
from app.api.deps import get_provider, get_session
from app.api.security import current_token, require_token
from app.main import app
from app.store.repos.order_repo import STATUS_PENDING_CONFIRMATION, create_order
from tests.api.conftest import FakeProvider


@pytest.fixture
def token_env(tmp_path, monkeypatch):
    """把 token 文件钉在 tmp_path 下(而非仓库里的相对 db_path),并清掉进程级
    缓存,保证每个测试拿到独立、确定性的 token。"""
    monkeypatch.setenv("STOCKAGENT_DB_PATH", str(tmp_path / "stockagent.db"))
    security._TOKEN_CACHE.clear()
    yield
    security._TOKEN_CACHE.clear()


@pytest.fixture
def unsecured_client(session, token_env):
    """不覆盖 require_token 的 client——唯一能证明门禁真正拦截的方式。"""
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_provider] = lambda: FakeProvider()
    app.dependency_overrides.pop(require_token, None)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _seed_pending(session, symbol="AAPL", side="buy", shares=1, as_of=dt.date(2026, 1, 5)):
    row = create_order(session, as_of, symbol, side, shares,
                       STATUS_PENDING_CONFIRMATION, "semi_auto")
    session.commit()
    return row.id


def _pending_ids(session) -> list[int]:
    from app.store.repos.order_repo import get_orders_by_status
    return [o.id for o in get_orders_by_status(session, STATUS_PENDING_CONFIRMATION)]


def test_approve_without_token_is_forbidden(unsecured_client, session):
    order_id = _seed_pending(session)
    resp = unsecured_client.post(f"/api/orders/{order_id}/approve")
    assert resp.status_code == 403
    # 无副作用:订单仍然待批,没有被悄悄批准
    assert _pending_ids(session) == [order_id]


def test_reject_without_token_is_forbidden(unsecured_client, session):
    order_id = _seed_pending(session)
    resp = unsecured_client.post(f"/api/orders/{order_id}/reject")
    assert resp.status_code == 403
    assert _pending_ids(session) == [order_id]


def test_mode_change_without_token_is_forbidden(unsecured_client):
    resp = unsecured_client.post("/api/settings/mode", json={"mode": "semi_auto"})
    assert resp.status_code == 403
    assert unsecured_client.get("/api/settings").json()["mode"] == "advisory"


def test_state_change_with_correct_token_succeeds(unsecured_client, token_env):
    token = current_token()
    resp = unsecured_client.post("/api/settings/mode", json={"mode": "semi_auto"},
                                 headers={"X-Stock-Agent-Token": token})
    assert resp.status_code == 200
    assert resp.json()["mode"] == "semi_auto"


def test_get_endpoints_do_not_require_token(unsecured_client):
    assert unsecured_client.get("/api/dashboard").status_code == 200
    assert unsecured_client.get("/api/settings").status_code == 200


def test_execution_backend_switch_without_token_is_forbidden(unsecured_client, session):
    from app.store.repos.settings_repo import get_execution_backend

    resp = unsecured_client.post("/api/execution/backend", json={"backend": "futu_paper"})
    assert resp.status_code == 403
    # 无副作用:没有被悄悄切到 futu_paper
    assert get_execution_backend(session) == "paper"


def test_execution_backend_switch_with_correct_token_succeeds(unsecured_client, token_env):
    token = current_token()
    resp = unsecured_client.post("/api/execution/backend", json={"backend": "futu_paper"},
                                 headers={"X-Stock-Agent-Token": token})
    assert resp.status_code == 200
    assert resp.json()["backend"] == "futu_paper"


def test_execution_get_does_not_require_token(unsecured_client):
    assert unsecured_client.get("/api/execution").status_code == 200


def test_settle_without_token_is_forbidden(unsecured_client):
    resp = unsecured_client.post("/api/orders/settle")
    assert resp.status_code == 403


def test_settle_with_correct_token_succeeds(unsecured_client, token_env):
    token = current_token()
    resp = unsecured_client.post("/api/orders/settle", headers={"X-Stock-Agent-Token": token})
    assert resp.status_code == 200
    assert resp.json() == {"fills": [], "count": 0}


def test_watchdog_without_token_is_forbidden(unsecured_client):
    resp = unsecured_client.post("/api/watchdog")
    assert resp.status_code == 403


def test_watchdog_with_correct_token_succeeds(unsecured_client, token_env):
    token = current_token()
    resp = unsecured_client.post("/api/watchdog", headers={"X-Stock-Agent-Token": token})
    assert resp.status_code == 200
    assert "healthy" in resp.json()


def test_run_signals_without_token_is_forbidden(unsecured_client):
    resp = unsecured_client.post("/api/signals/run")
    assert resp.status_code == 403
