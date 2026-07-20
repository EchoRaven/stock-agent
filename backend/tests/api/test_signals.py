import datetime as dt

from app.store.models import SignalRow

D = dt.date(2026, 1, 5)


def test_signals_returns_seeded_snapshot_for_date(client, session):
    session.add(SignalRow(as_of=D, symbol="AAPL", rank=1, total=0.9, parts_json="{}"))
    session.add(SignalRow(as_of=D, symbol="MSFT", rank=2, total=0.7, parts_json="{}"))
    session.commit()
    resp = client.get("/api/signals", params={"date": D.isoformat()})
    assert resp.status_code == 200
    body = resp.json()
    assert [s["symbol"] for s in body] == ["AAPL", "MSFT"]
    assert body[0]["total"] == 0.9


def test_signals_empty_for_date_with_no_snapshot(client, session):
    resp = client.get("/api/signals", params={"date": "2020-01-01"})
    assert resp.status_code == 200
    assert resp.json() == []
