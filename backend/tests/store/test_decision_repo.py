"""decision_repo.get_recent_decisions:决策历史浏览用的最新优先查询(只读)。"""
import datetime as dt

import pytest

from app.store.db import init_db, make_engine, make_session_factory
from app.store.models import DecisionRow
from app.store.repos.decision_repo import get_recent_decisions, save_decision

D1 = dt.date(2026, 7, 10)
D2 = dt.date(2026, 7, 15)
D3 = dt.date(2026, 7, 17)


@pytest.fixture
def session():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


# ---------------------------------------------------------------------------
# held: records whether we held the symbol at decision time (scorecard needs
# this to tell "chose not to sell" from "structurally could not sell" — see
# app/services/scorecard_service.py). Must round-trip True/False/None exactly;
# None means "unknown" (legacy row), never silently False.
# ---------------------------------------------------------------------------

def test_save_decision_persists_held_true(session):
    row = save_decision(session, D1, "AAPL", "sell", 0.8, "advisory", "{}", held=True)
    session.commit()
    fetched = session.get(DecisionRow, row.id)
    assert fetched.held is True


def test_save_decision_persists_held_false(session):
    row = save_decision(session, D1, "AAPL", "buy", 0.8, "advisory", "{}", held=False)
    session.commit()
    fetched = session.get(DecisionRow, row.id)
    assert fetched.held is False


def test_save_decision_held_defaults_to_none(session):
    # caller omitted held entirely (legacy call site) -> must read back as
    # None (unknown), not False.
    row = save_decision(session, D1, "AAPL", "hold", 0.5, "advisory", "{}")
    session.commit()
    fetched = session.get(DecisionRow, row.id)
    assert fetched.held is None


def test_get_recent_decisions_newest_first(session):
    save_decision(session, D1, "AAPL", "buy", 0.8, "advisory", "{}")
    save_decision(session, D2, "MSFT", "hold", 0.5, "advisory", "{}")
    save_decision(session, D3, "AAPL", "sell", 0.6, "advisory", "{}")
    rows = get_recent_decisions(session)
    # 按 created_at desc:最后写入的排最前
    assert [r.symbol for r in rows] == ["AAPL", "MSFT", "AAPL"]
    assert [r.as_of for r in rows] == [D3, D2, D1]


def test_get_recent_decisions_filters_by_symbol(session):
    save_decision(session, D1, "AAPL", "buy", 0.8, "advisory", "{}")
    save_decision(session, D2, "MSFT", "hold", 0.5, "advisory", "{}")
    save_decision(session, D3, "AAPL", "sell", 0.6, "advisory", "{}")
    rows = get_recent_decisions(session, symbol="AAPL")
    assert [r.symbol for r in rows] == ["AAPL", "AAPL"]
    assert [r.as_of for r in rows] == [D3, D1]


def test_get_recent_decisions_applies_limit(session):
    for _ in range(5):
        save_decision(session, D1, "AAPL", "buy", 0.5, "advisory", "{}")
    rows = get_recent_decisions(session, limit=2)
    assert len(rows) == 2


def test_get_recent_decisions_default_limit_is_50(session):
    for _ in range(60):
        save_decision(session, D1, "AAPL", "buy", 0.5, "advisory", "{}")
    rows = get_recent_decisions(session)
    assert len(rows) == 50


def test_get_recent_decisions_tiebreak_by_id_desc(session):
    row1 = save_decision(session, D1, "AAPL", "buy", 0.5, "advisory", "{}")
    row2 = save_decision(session, D1, "MSFT", "buy", 0.5, "advisory", "{}")
    same = dt.datetime(2026, 7, 17, 12, 0, 0)
    row1.created_at = same
    row2.created_at = same
    session.flush()
    rows = get_recent_decisions(session)
    assert [r.symbol for r in rows] == ["MSFT", "AAPL"]  # 更高 id 排前


def test_get_recent_decisions_empty(session):
    assert get_recent_decisions(session) == []
