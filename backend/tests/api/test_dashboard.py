import datetime as dt

from app.store.repos.order_repo import STATUS_PENDING_CONFIRMATION, create_order


def test_dashboard_shape(client, session):
    resp = client.get("/api/dashboard")
    assert resp.status_code == 200
    body = resp.json()
    for key in ("mode", "positions", "cash", "equity",
               "circuit_breaker_tripped", "pending_orders_count", "as_of"):
        assert key in body
    assert body["mode"] == "advisory"
    assert body["positions"] == {}
    assert body["circuit_breaker_tripped"] is False
    assert body["pending_orders_count"] == 0
    assert body["cash"] == 100_000.0


def test_dashboard_counts_pending_orders(client, session):
    create_order(session, dt.date(2026, 1, 5), "AAPL", "buy", 1,
                STATUS_PENDING_CONFIRMATION, "semi_auto")
    session.commit()
    resp = client.get("/api/dashboard")
    assert resp.json()["pending_orders_count"] == 1
