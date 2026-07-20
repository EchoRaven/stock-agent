"""安全红线覆盖:approve 只认服务端派生 as_of/价格,客户端传入的一律被忽略。"""
import datetime as dt

from app.execution import order_manager
from app.store.repos.order_repo import (STATUS_PENDING_CONFIRMATION, create_order,
                                        get_order)


def _seed_pending(session, symbol="AAPL", side="buy", shares=1, as_of=dt.date(2026, 1, 5)):
    row = create_order(session, as_of, symbol, side, shares,
                       STATUS_PENDING_CONFIRMATION, "semi_auto")
    session.commit()
    return row.id


def test_list_orders_defaults_to_pending(client, session):
    order_id = _seed_pending(session)
    resp = client.get("/api/orders")
    assert resp.status_code == 200
    assert [o["id"] for o in resp.json()] == [order_id]


def test_list_orders_by_status_filter(client, session):
    _seed_pending(session)
    resp = client.get("/api/orders", params={"status": "rejected"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_approve_ignores_client_supplied_as_of_and_prices(client, session):
    order_id = _seed_pending(session, shares=1)
    bogus_payload = {"as_of": "2099-01-01", "prices": {"AAPL": 999999.0}}
    resp = client.post(f"/api/orders/{order_id}/approve", json=bogus_payload)
    assert resp.status_code == 200
    order = resp.json()["order"]
    # 服务端 as_of 绝不是客户端塞入的伪造未来日期
    assert order["as_of"] != "2099-01-01"
    # 走过闸门被真正批准提交(用的是服务端取的价,不是 payload 里的 999999)
    assert order["status"] == "submitted"


def test_approve_non_pending_order_returns_409(client, session):
    order_id = _seed_pending(session)
    first = client.post(f"/api/orders/{order_id}/approve")
    assert first.status_code == 200
    second = client.post(f"/api/orders/{order_id}/approve")
    assert second.status_code == 409


def test_approve_missing_order_returns_409(client, session):
    resp = client.post("/api/orders/999999/approve")
    assert resp.status_code == 409


def test_reject_transitions_status_and_records_reason(client, session):
    order_id = _seed_pending(session)
    resp = client.post(f"/api/orders/{order_id}/reject", json={"reason": "test reason"})
    assert resp.status_code == 200
    order = resp.json()["order"]
    assert order["status"] == "rejected"
    assert order["reason"] == "test reason"


def test_reject_non_pending_order_returns_409(client, session):
    order_id = _seed_pending(session)
    client.post(f"/api/orders/{order_id}/reject")
    resp = client.post(f"/api/orders/{order_id}/reject")
    assert resp.status_code == 409


def test_settle_with_no_submitted_orders_returns_empty(client, session):
    resp = client.post("/api/orders/settle")
    assert resp.status_code == 200
    assert resp.json() == {"fills": [], "count": 0}


def test_settle_fills_submitted_orders(client, session):
    order_id = _seed_pending(session, symbol="AAPL", shares=5)
    approved = client.post(f"/api/orders/{order_id}/approve")
    assert approved.json()["order"]["status"] == "submitted"

    resp = client.post("/api/orders/settle")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["fills"][0]["symbol"] == "AAPL"
    assert body["fills"][0]["shares"] == 5


# ---------------------------------------------------------------------------
# 可插拔执行后端(futu_paper 等)故障必须 502(网关/后端不可用),不是裸 500;
# 且响应体不得回显异常原始文本(可能带连接细节/凭据片段)。
# ---------------------------------------------------------------------------


class _BrokenBroker:
    """Broker 存根:submit/process_fills 均模拟连接失败(如 futu-api 未装好/
    OpenD 未监听),异常信息里故意塞一个"看起来像密钥"的片段,验证它不泄漏。"""

    _SECRET = "unlock_pwd=s3cr3t-token-should-not-leak"

    def submit(self, session, order):
        raise RuntimeError(f"OpenD connection refused: {self._SECRET}")

    def process_fills(self, session, fill_date, open_prices):
        raise RuntimeError(f"OpenD connection refused: {self._SECRET}")


def test_approve_returns_502_when_broker_unavailable(client, session, monkeypatch):
    order_id = _seed_pending(session)
    monkeypatch.setattr(order_manager, "_get_broker", lambda s: _BrokenBroker())
    resp = client.post(f"/api/orders/{order_id}/approve")
    assert resp.status_code == 502
    assert "s3cr3t" not in resp.text
    # 无副作用:订单没有被静默批准成 submitted
    assert get_order(session, order_id).status != "submitted"


def test_settle_returns_502_when_broker_unavailable(client, session, monkeypatch):
    order_id = _seed_pending(session, symbol="AAPL", shares=5)
    approved = client.post(f"/api/orders/{order_id}/approve")
    assert approved.json()["order"]["status"] == "submitted"

    monkeypatch.setattr(order_manager, "_get_broker", lambda s: _BrokenBroker())
    resp = client.post("/api/orders/settle")
    assert resp.status_code == 502
    assert "s3cr3t" not in resp.text
