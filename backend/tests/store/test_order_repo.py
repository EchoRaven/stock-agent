import datetime as dt

import pytest

from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.order_repo import (ACTIVE_STATUSES, STATUS_APPROVED,
                                        STATUS_FILLED, STATUS_PENDING_CONFIRMATION,
                                        STATUS_REJECTED, STATUS_SUBMITTED, STATUSES,
                                        DuplicateOrderError, buy_symbols_today,
                                        create_order, get_order, get_orders_by_status,
                                        has_active_order, update_status)

D = dt.date(2026, 7, 17)
D1 = dt.date(2026, 7, 20)


@pytest.fixture
def session():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


def test_status_constants():
    assert set(ACTIVE_STATUSES) == {"pending_confirmation", "approved", "submitted"}
    assert set(STATUSES) == {"pending_confirmation", "approved", "rejected",
                             "submitted", "filled", "cancelled"}


def test_create_and_get(session):
    row = create_order(session, D, "AAPL", "buy", 10, STATUS_PENDING_CONFIRMATION,
                       "semi_auto", decision_id=7)
    assert row.id is not None
    fetched = get_order(session, row.id)
    assert fetched.symbol == "AAPL" and fetched.status == STATUS_PENDING_CONFIRMATION
    assert fetched.decision_id == 7 and fetched.mode == "semi_auto"
    assert get_order(session, 999) is None
    with pytest.raises(ValueError):
        create_order(session, D, "MSFT", "buy", 10, "weird", "semi_auto")


def test_duplicate_active_order_blocked(session):
    create_order(session, D, "AAPL", "buy", 10, STATUS_PENDING_CONFIRMATION, "semi_auto")
    assert has_active_order(session, D, "AAPL")
    with pytest.raises(DuplicateOrderError):
        create_order(session, D, "AAPL", "sell", 5, STATUS_SUBMITTED, "full_auto")
    # 审计用 rejected 单不受重复保护限制;不同日/不同标的不受影响
    create_order(session, D, "AAPL", "buy", 10, STATUS_REJECTED, "full_auto", reason="over cap")
    create_order(session, D1, "AAPL", "buy", 10, STATUS_PENDING_CONFIRMATION, "semi_auto")
    create_order(session, D, "MSFT", "buy", 10, STATUS_PENDING_CONFIRMATION, "semi_auto")


def test_terminal_order_frees_the_slot(session):
    row = create_order(session, D, "AAPL", "buy", 10, STATUS_PENDING_CONFIRMATION, "semi_auto")
    update_status(session, row.id, STATUS_REJECTED, reason="user")
    assert not has_active_order(session, D, "AAPL")
    create_order(session, D, "AAPL", "buy", 10, STATUS_PENDING_CONFIRMATION, "semi_auto")


def test_update_status_and_reason(session):
    row = create_order(session, D, "AAPL", "buy", 10, STATUS_PENDING_CONFIRMATION, "semi_auto")
    out = update_status(session, row.id, STATUS_APPROVED)
    assert out.status == STATUS_APPROVED and out.reason == ""
    out = update_status(session, row.id, STATUS_REJECTED, reason="risk gate")
    assert out.reason == "risk gate"
    with pytest.raises(ValueError):
        update_status(session, row.id, "weird")
    with pytest.raises(ValueError):
        update_status(session, 999, STATUS_REJECTED)


def test_get_orders_by_status_ordered(session):
    a = create_order(session, D, "AAPL", "buy", 1, STATUS_PENDING_CONFIRMATION, "semi_auto")
    b = create_order(session, D, "MSFT", "buy", 1, STATUS_PENDING_CONFIRMATION, "semi_auto")
    assert [r.id for r in get_orders_by_status(session, STATUS_PENDING_CONFIRMATION)] == [a.id, b.id]
    assert get_orders_by_status(session, STATUS_FILLED) == []


def test_buy_symbols_today_counts_active_and_filled_only(session):
    create_order(session, D, "AAPL", "buy", 1, STATUS_PENDING_CONFIRMATION, "semi_auto")
    create_order(session, D, "MSFT", "buy", 1, STATUS_FILLED, "full_auto")
    create_order(session, D, "NVDA", "buy", 1, STATUS_REJECTED, "full_auto", reason="cap")
    create_order(session, D, "AMD", "sell", 1, STATUS_SUBMITTED, "full_auto")
    create_order(session, D1, "GOOG", "buy", 1, STATUS_SUBMITTED, "full_auto")
    assert buy_symbols_today(session, D) == {"AAPL", "MSFT"}
