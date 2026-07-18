import datetime as dt

import pytest

from app.execution.account_state import build_account_state
from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.order_repo import STATUS_PENDING_CONFIRMATION, create_order
from app.store.repos.paper_repo import add_fill, get_account, set_position
from app.store.repos.settings_repo import update_risk_params

D = dt.date(2026, 7, 17)


@pytest.fixture
def session():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


def test_positions_valued_at_latest_price_with_avg_cost_fallback(session):
    set_position(session, "AAPL", 10, 90.0)
    set_position(session, "MSFT", 2, 50.0)
    state = build_account_state(session, D, {"AAPL": 100.0})
    assert state.position_values["AAPL"] == pytest.approx(1000.0)
    assert state.position_values["MSFT"] == pytest.approx(100.0)  # 缺价保守用 avg_cost
    assert state.equity() == pytest.approx(100_000.0 + 1100.0)
    # finding #6:报价缺失的持仓被采集进 stale_priced_symbols(供 StaleQuoteRule 拦买单)
    assert state.stale_priced_symbols == frozenset({"MSFT"})


def test_cash_seeded_from_settings_initial_cash(session):
    update_risk_params(session, initial_cash=50_000.0)
    state = build_account_state(session, D, {})
    assert state.cash == 50_000.0


def test_breaker_evaluated_and_persisted(session):
    set_position(session, "AAPL", 100, 100.0)
    first = build_account_state(session, D, {"AAPL": 100.0})   # day start = 110_000
    assert first.breaker_tripped is False
    crashed = build_account_state(session, D, {"AAPL": 20.0})  # 权益 102_000,回撤 7.3%
    assert crashed.breaker_tripped is True
    assert get_account(session, 100_000.0).breaker_tripped_on == D


def test_new_buy_symbols_and_last_sell_dates_wired(session):
    create_order(session, D, "NVDA", "buy", 5, STATUS_PENDING_CONFIRMATION, "semi_auto")
    add_fill(session, 1, D, "AMD", "sell", 3, 10.0)
    state = build_account_state(session, D, {})
    assert state.new_buy_symbols_today == frozenset({"NVDA"})
    assert state.last_sell_dates == {"AMD": D}
