"""GET/POST /api/execution —— 安全红线覆盖:UI 只能在 paper/futu_paper 之间切换,
真实资金(REAL)完全触碰不到这个开关(只在 FutuBroker 内部靠 env-only
futu_allow_real + futu_unlock_pwd 硬门控)。token 门禁另见 tests/api/test_security.py。
"""
from app.execution.futu_broker import FutuBroker
from app.execution.order_manager import _get_broker
from app.execution.paper import PaperBroker
from app.store.repos.settings_repo import EXECUTION_BACKENDS, get_execution_backend


def test_execution_default_is_paper(client, session):
    resp = client.get("/api/execution")
    assert resp.status_code == 200
    body = resp.json()
    assert body["backend"] == "paper"
    assert body["available_backends"] == ["paper", "futu_paper"]
    assert isinstance(_get_broker(session), PaperBroker)


def test_execution_response_includes_futu_meta(client):
    body = client.get("/api/execution").json()
    futu = body["futu"]
    for key in ("host", "port", "trd_env", "allow_real", "opend_reachable"):
        assert key in futu
    assert futu["allow_real"] is False
    # 沙箱里没有真实 OpenD 监听,且联网被全局熔断——探测必须优雅落回 False,不抛异常。
    assert futu["opend_reachable"] is False


def test_switch_to_futu_paper_persists(client, session):
    resp = client.post("/api/execution/backend", json={"backend": "futu_paper"})
    assert resp.status_code == 200
    assert resp.json()["backend"] == "futu_paper"
    assert client.get("/api/execution").json()["backend"] == "futu_paper"
    assert get_execution_backend(session) == "futu_paper"
    assert isinstance(_get_broker(session), FutuBroker)


def test_switch_back_to_paper_returns_paper_broker(client, session):
    client.post("/api/execution/backend", json={"backend": "futu_paper"})
    resp = client.post("/api/execution/backend", json={"backend": "paper"})
    assert resp.status_code == 200
    assert resp.json()["backend"] == "paper"
    assert isinstance(_get_broker(session), PaperBroker)


def test_cannot_enable_real_money_from_ui(client, session):
    # 红线断言:合法值集合本身就没有真实资金选项。
    assert EXECUTION_BACKENDS == ("paper", "futu_paper")
    for bogus in ("futu_real", "real", "REAL", "anything"):
        resp = client.post("/api/execution/backend", json={"backend": bogus})
        assert resp.status_code == 400
        # 拒绝之后没有半吊子写入——仍是默认 paper
        assert get_execution_backend(session) == "paper"
    assert client.get("/api/execution").json()["backend"] == "paper"
