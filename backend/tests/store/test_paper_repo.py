import datetime as dt

import pytest

from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.paper_repo import (add_fill, get_account, get_fills,
                                        get_position, get_positions,
                                        last_sell_dates, set_position)

D = dt.date(2026, 7, 17)
D1 = dt.date(2026, 7, 20)


@pytest.fixture
def session():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


def test_get_account_creates_singleton_with_initial_cash(session):
    account = get_account(session, 50_000.0)
    assert account.id == 1 and account.cash == 50_000.0
    assert get_account(session, 999.0).cash == 50_000.0  # 已存在则忽略 initial_cash


def test_get_account_rejects_nonpositive_seed(session):
    with pytest.raises(ValueError):
        get_account(session, 0.0)


def test_set_position_upsert_and_delete(session):
    set_position(session, "AAPL", 10, 100.0)
    set_position(session, "AAPL", 20, 105.0)
    positions = get_positions(session)
    assert positions["AAPL"].shares == 20 and positions["AAPL"].avg_cost == 105.0
    set_position(session, "AAPL", 0, 0.0)
    assert get_positions(session) == {}


def test_add_fill_and_get_fills(session):
    add_fill(session, 1, D, "AAPL", "buy", 10, 100.0)
    add_fill(session, 2, D1, "AAPL", "sell", 5, 110.0)
    assert [f.side for f in get_fills(session)] == ["buy", "sell"]
    assert [f.order_id for f in get_fills(session, D1)] == [2]


def test_get_position_hit_and_miss(session):
    # miss:未持仓
    assert get_position(session, "AAPL") is None
    set_position(session, "AAPL", 10, 100.0)
    set_position(session, "MSFT", 5, 50.0)
    # hit:单标的查询与 get_positions() 结果一致
    row = get_position(session, "AAPL")
    assert row is not None and row.shares == 10 and row.avg_cost == 100.0
    # 不返回其它标的
    assert get_position(session, "MSFT").shares == 5
    # shares 归零后视为 miss(与 get_positions() 的 shares>0 过滤语义一致)
    set_position(session, "AAPL", 0, 0.0)
    assert get_position(session, "AAPL") is None


def test_last_sell_dates_latest_per_symbol(session):
    add_fill(session, 1, D, "AAPL", "sell", 5, 100.0)
    add_fill(session, 2, D1, "AAPL", "sell", 5, 100.0)
    add_fill(session, 3, D, "MSFT", "buy", 5, 100.0)
    assert last_sell_dates(session) == {"AAPL": D1}
