import datetime as dt
import json

import pytest

from app.screener.base import RuleResult, SymbolScore
from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.decision_repo import get_decisions, save_decision
from app.store.repos.report_repo import get_report, save_report
from app.store.repos.signal_repo import get_signals, save_signals

D = dt.date(2026, 7, 17)


@pytest.fixture
def session():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


def _scores():
    return [
        SymbolScore("AAPL", 0.9, {"trend": RuleResult(1.0, "up")}),
        SymbolScore("MSFT", 0.7, {"trend": RuleResult(0.7, "ok")}),
    ]


def test_save_signals_and_read_back(session):
    assert save_signals(session, D, _scores()) == 2
    rows = get_signals(session, D)
    assert [(r.rank, r.symbol) for r in rows] == [(1, "AAPL"), (2, "MSFT")]
    assert json.loads(rows[0].parts_json)["trend"]["score"] == 1.0


def test_save_signals_overwrites_same_day(session):
    save_signals(session, D, _scores())
    save_signals(session, D, _scores()[:1])
    assert len(get_signals(session, D)) == 1
    assert get_signals(session, dt.date(2026, 7, 16)) == []


def test_save_decision_assigns_id_and_orders(session):
    row1 = save_decision(session, D, "AAPL", "buy", 0.8, "advisory", "{}")
    row2 = save_decision(session, D, "MSFT", "hold", 0.5, "advisory", "{}")
    assert row1.id is not None and row2.id > row1.id
    assert [r.symbol for r in get_decisions(session, D)] == ["AAPL", "MSFT"]
    assert get_decisions(session, dt.date(2026, 7, 16)) == []


def test_report_upsert(session):
    save_report(session, D, "v1")
    row = save_report(session, D, "v2")
    assert row.content_md == "v2"
    assert get_report(session, D).content_md == "v2"
    assert get_report(session, dt.date(2026, 7, 16)) is None
