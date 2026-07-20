import datetime as dt

from app.store.models import SignalRow
from app.util.trading_day import et_trading_day

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


def test_run_signals_persists_and_returns(client, session):
    # client fixture 注入的 FakeProvider 返回固定收盘价的整段日线(全离线,见
    # tests/api/conftest.py),足够跑完整条筛选流水线。
    resp = client.post("/api/signals/run", json={"universe": ["AAPL", "MSFT"], "top_n": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    symbols = {s["symbol"] for s in body}
    assert symbols == {"AAPL", "MSFT"}

    today = et_trading_day(dt.datetime.now(dt.UTC))
    persisted = client.get("/api/signals", params={"date": today.isoformat()})
    assert persisted.status_code == 200
    assert {s["symbol"] for s in persisted.json()} == {"AAPL", "MSFT"}


def test_run_signals_defaults_universe_and_top_n(client, session):
    resp = client.post("/api/signals/run", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) > 0
    assert len(body) <= 10  # default settings.top_n


def test_run_signals_with_no_body_at_all_uses_defaults(client, session):
    resp = client.post("/api/signals/run")
    assert resp.status_code == 200
    assert len(resp.json()) > 0
