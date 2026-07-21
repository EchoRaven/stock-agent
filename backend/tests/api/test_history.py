"""GET /api/decisions + GET /api/performance —— 只读:决策历史浏览 + 从
trade_review 复盘条目聚合出的业绩战绩单。

只读约定(与 /api/dashboard、/api/memory 一致):不设 token 门禁,不发起任何
网络请求(/api/performance 的 equity 用持仓 avg_cost 近似,不取实时行情)。
"""
import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_provider, get_session
from app.api.security import require_token
from app.main import app
from app.store.repos.decision_repo import save_decision
from app.store.repos.memory_repo import add_entry
from tests.api.conftest import FakeProvider


@pytest.fixture
def unsecured_client(session):
    """不覆盖 require_token 的 client——唯一能证明只读端点真的不设门禁的方式。"""
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_provider] = lambda: FakeProvider()
    app.dependency_overrides.pop(require_token, None)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _seed_review(session, realized_pnl, holding_days, created_at, symbol="AAPL"):
    row = add_entry(
        session, "trade_review", f"review-{symbol}-{realized_pnl}", "post-mortem",
        symbol=symbol,
        evidence={
            "sell_fill_id": 1, "symbol": symbol, "realized_pnl": realized_pnl,
            "realized_pnl_pct": 0.01, "holding_days": holding_days,
            "buy_vwap": 10.0, "sell_price": 11.0, "shares": 100,
        },
    )
    row.created_at = created_at
    session.flush()
    return row


# ---------------------------------------------------------------------------
# GET /api/decisions
# ---------------------------------------------------------------------------

def test_list_decisions_returns_newest_first_with_chair_verdict(client, session):
    payload = json.dumps({"chair": {"verdict": "buy signal strong"}})
    save_decision(session, dt.date(2026, 7, 10), "AAPL", "buy", 0.8, "advisory", payload)
    save_decision(session, dt.date(2026, 7, 15), "MSFT", "hold", 0.5, "advisory", "{}")
    session.commit()

    resp = client.get("/api/decisions")
    assert resp.status_code == 200
    body = resp.json()
    assert [d["symbol"] for d in body] == ["MSFT", "AAPL"]

    aapl = next(d for d in body if d["symbol"] == "AAPL")
    assert aapl["chair_verdict"] == "buy signal strong"
    assert aapl["action"] == "buy"
    assert aapl["confidence"] == 0.8
    assert aapl["mode"] == "advisory"
    assert aapl["as_of"] == "2026-07-10"
    assert "created_at" in aapl and "id" in aapl

    msft = next(d for d in body if d["symbol"] == "MSFT")
    assert msft["chair_verdict"] == ""  # no chair key in payload


def test_list_decisions_filters_by_symbol_and_uppercases(client, session):
    save_decision(session, dt.date(2026, 7, 10), "AAPL", "buy", 0.8, "advisory", "{}")
    save_decision(session, dt.date(2026, 7, 10), "MSFT", "hold", 0.5, "advisory", "{}")
    session.commit()

    resp = client.get("/api/decisions", params={"symbol": "aapl"})
    assert resp.status_code == 200
    assert [d["symbol"] for d in resp.json()] == ["AAPL"]


def test_list_decisions_limit_applies(client, session):
    for i in range(5):
        save_decision(session, dt.date(2026, 7, 10), "AAPL", "buy", 0.5, "advisory", "{}")
    session.commit()

    resp = client.get("/api/decisions", params={"limit": 2})
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_list_decisions_limit_out_of_bounds_rejected(client, session):
    assert client.get("/api/decisions", params={"limit": 0}).status_code == 422
    assert client.get("/api/decisions", params={"limit": 501}).status_code == 422


def test_list_decisions_malformed_payload_json_no_500(client, session):
    save_decision(session, dt.date(2026, 7, 10), "AAPL", "buy", 0.8, "advisory", "not-json{{")
    session.commit()

    resp = client.get("/api/decisions")
    assert resp.status_code == 200
    assert resp.json()[0]["chair_verdict"] == ""


def test_list_decisions_chair_not_dict_no_500(client, session):
    save_decision(session, dt.date(2026, 7, 10), "AAPL", "buy", 0.8, "advisory",
                  json.dumps({"chair": "not-a-dict"}))
    save_decision(session, dt.date(2026, 7, 11), "MSFT", "buy", 0.8, "advisory",
                  json.dumps(["not", "a", "dict", "either"]))
    session.commit()

    resp = client.get("/api/decisions")
    assert resp.status_code == 200
    assert all(d["chair_verdict"] == "" for d in resp.json())


def test_list_decisions_chair_verdict_truncated(client, session):
    long_verdict = "x" * 500
    save_decision(session, dt.date(2026, 7, 10), "AAPL", "buy", 0.8, "advisory",
                  json.dumps({"chair": {"verdict": long_verdict}}))
    session.commit()

    resp = client.get("/api/decisions")
    assert len(resp.json()[0]["chair_verdict"]) <= 300


def test_list_decisions_empty(client, session):
    resp = client.get("/api/decisions")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /api/decisions/scorecard
# ---------------------------------------------------------------------------

def _seed_decisions(session, n, action="buy", confidence=0.8, as_of=dt.date(2026, 7, 10)):
    for i in range(n):
        save_decision(session, as_of, f"SYM{i}", action, confidence, "advisory", "{}")


def test_scorecard_route_returns_expected_shape(client, session):
    _seed_decisions(session, 12)
    session.commit()

    resp = client.get("/api/decisions/scorecard")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, dict)  # not a list -> proves it's not the /decisions list route
    for key in ("total", "by_action", "by_action_pct", "confidence", "histogram",
               "by_mode", "gate", "flags"):
        assert key in body
    assert body["total"] == 12


def test_scorecard_route_not_shadowed_by_decisions_list_route(client, session):
    _seed_decisions(session, 12)
    session.commit()

    scorecard_body = client.get("/api/decisions/scorecard").json()
    list_body = client.get("/api/decisions").json()

    assert isinstance(scorecard_body, dict) and "flags" in scorecard_body
    assert isinstance(list_body, list) and len(list_body) == 12


def test_scorecard_route_empty_db_returns_200_no_crash(client, session):
    resp = client.get("/api/decisions/scorecard")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["confidence"]["mean"] is None
    assert body["flags"][0]["code"] == "insufficient_data"


def test_scorecard_route_days_param_filters(client, session):
    save_decision(session, dt.date(2026, 6, 1), "OLD", "buy", 0.8, "advisory", "{}")
    save_decision(session, dt.date(2026, 7, 19), "RECENT", "buy", 0.8, "advisory", "{}")
    session.commit()

    resp = client.get("/api/decisions/scorecard", params={"days": 5})
    assert resp.status_code == 200
    # window_days echoes back what was requested regardless of how many rows match
    assert resp.json()["window_days"] == 5


def test_scorecard_route_does_not_require_token(unsecured_client):
    assert unsecured_client.get("/api/decisions/scorecard").status_code == 200


# ---------------------------------------------------------------------------
# GET /api/performance
# ---------------------------------------------------------------------------

def test_performance_empty_returns_zeros_no_crash(client, session):
    resp = client.get("/api/performance")
    assert resp.status_code == 200
    body = resp.json()
    assert body["closed_trades"] == 0
    assert body["realized_pnl_total"] == 0
    assert body["win_rate"] is None
    assert body["wins"] == 0
    assert body["losses"] == 0
    assert body["avg_win"] is None
    assert body["avg_loss"] is None
    assert body["avg_holding_days"] is None
    assert body["cumulative_pnl_series"] == []
    assert body["cash"] == 100_000.0
    assert body["open_positions"] == 0
    assert body["open_positions_cost_value"] == 0
    assert body["equity_at_cost"] == 100_000.0
    assert body["initial_cash"] == 100_000.0


def test_performance_aggregates_closed_trades(client, session):
    _seed_review(session, 100.0, 5, dt.datetime(2026, 7, 1, 10, 0, 0))
    _seed_review(session, -40.0, 3, dt.datetime(2026, 7, 5, 10, 0, 0))
    _seed_review(session, 60.0, 10, dt.datetime(2026, 7, 10, 10, 0, 0))
    session.commit()

    resp = client.get("/api/performance")
    assert resp.status_code == 200
    body = resp.json()
    assert body["closed_trades"] == 3
    assert body["realized_pnl_total"] == pytest.approx(120.0)
    assert body["wins"] == 2
    assert body["losses"] == 1
    assert body["win_rate"] == pytest.approx(2 / 3)
    assert body["avg_win"] == pytest.approx(80.0)
    assert body["avg_loss"] == pytest.approx(-40.0)
    assert body["avg_holding_days"] == pytest.approx(6.0)


def test_performance_cumulative_series_ascending_by_date(client, session):
    _seed_review(session, 100.0, 5, dt.datetime(2026, 7, 1, 10, 0, 0))
    _seed_review(session, -40.0, 3, dt.datetime(2026, 7, 5, 10, 0, 0))
    _seed_review(session, 60.0, 10, dt.datetime(2026, 7, 10, 10, 0, 0))
    session.commit()

    series = client.get("/api/performance").json()["cumulative_pnl_series"]
    assert [p["date"] for p in series] == ["2026-07-01", "2026-07-05", "2026-07-10"]
    assert [p["cum_pnl"] for p in series] == pytest.approx([100.0, 60.0, 120.0])
    # monotonic in count: a series point exists for each distinct day seen so far
    assert len(series) == 3


def test_performance_cumulative_series_same_day_keeps_last(client, session):
    same_day = dt.datetime(2026, 7, 1, 9, 0, 0)
    _seed_review(session, 100.0, 5, same_day)
    _seed_review(session, -30.0, 2, dt.datetime(2026, 7, 1, 15, 0, 0))
    session.commit()

    series = client.get("/api/performance").json()["cumulative_pnl_series"]
    assert len(series) == 1
    assert series[0]["date"] == "2026-07-01"
    assert series[0]["cum_pnl"] == pytest.approx(70.0)


def test_performance_skips_malformed_evidence_no_500(client, session):
    good = _seed_review(session, 50.0, 4, dt.datetime(2026, 7, 1, 10, 0, 0))
    assert good.id is not None
    # malformed JSON
    bad1 = add_entry(session, "trade_review", "bad-json", "x", evidence={"realized_pnl": 1.0})
    bad1.evidence_json = "{not valid json"
    # valid JSON, but missing realized_pnl
    add_entry(session, "trade_review", "missing-pnl", "x",
             evidence={"symbol": "AAPL", "holding_days": 1})
    # valid JSON, realized_pnl is not numeric
    add_entry(session, "trade_review", "bad-pnl-type", "x",
             evidence={"realized_pnl": "not-a-number"})
    session.commit()

    resp = client.get("/api/performance")
    assert resp.status_code == 200
    body = resp.json()
    assert body["closed_trades"] == 1
    assert body["realized_pnl_total"] == pytest.approx(50.0)


def test_performance_win_loss_only_fields_none_when_absent(client, session):
    _seed_review(session, -25.0, 2, dt.datetime(2026, 7, 1, 10, 0, 0))
    session.commit()

    body = client.get("/api/performance").json()
    assert body["wins"] == 0
    assert body["losses"] == 1
    assert body["avg_win"] is None
    assert body["avg_loss"] == pytest.approx(-25.0)


def test_performance_includes_open_positions_cost_value(client, session):
    from app.store.repos.paper_repo import set_position

    set_position(session, "AAPL", 10, 150.0)
    session.commit()

    body = client.get("/api/performance").json()
    assert body["open_positions"] == 1
    assert body["open_positions_cost_value"] == pytest.approx(1500.0)
    assert body["equity_at_cost"] == pytest.approx(100_000.0 + 1500.0)


# ---------------------------------------------------------------------------
# Read-only / offline guarantees
# ---------------------------------------------------------------------------

def test_decisions_and_performance_do_not_require_token(unsecured_client):
    assert unsecured_client.get("/api/decisions").status_code == 200
    assert unsecured_client.get("/api/performance").status_code == 200


def test_performance_call_does_not_change_cash(client, session):
    body1 = client.get("/api/performance").json()
    body2 = client.get("/api/performance").json()
    assert body1["cash"] == body2["cash"] == 100_000.0
