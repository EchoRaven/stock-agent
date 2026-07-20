"""安全红线覆盖:approve 只认服务端派生 as_of/价格,客户端传入的一律被忽略。"""
import datetime as dt

from app.store.repos.order_repo import STATUS_PENDING_CONFIRMATION, create_order


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
