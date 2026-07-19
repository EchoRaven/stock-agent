import datetime as dt

import pytest

import app.mcp.runtime as runtime
from app.mcp.tool_orders import get_pending_orders
from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.order_repo import (STATUS_PENDING_CONFIRMATION, STATUS_SUBMITTED,
                                        create_order)

D = dt.date(2026, 7, 17)


@pytest.fixture
def factory(monkeypatch):
    engine = make_engine(":memory:")
    init_db(engine)
    factory = make_session_factory(engine)
    monkeypatch.setattr(runtime, "open_session", lambda: factory())
    return factory


def test_empty_queue(factory):
    assert get_pending_orders() == {"pending": []}


def test_lists_pending_only(factory):
    with factory() as session:
        create_order(session, D, "AAPL", "buy", 10, STATUS_PENDING_CONFIRMATION, "semi_auto")
        create_order(session, D, "MSFT", "buy", 5, STATUS_SUBMITTED, "full_auto")
        session.commit()
    out = get_pending_orders()
    assert [o["symbol"] for o in out["pending"]] == ["AAPL"]
    assert out["pending"][0]["status"] == STATUS_PENDING_CONFIRMATION
