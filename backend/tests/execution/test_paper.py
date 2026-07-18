import datetime as dt

import pytest

from app.execution.paper import PaperBroker
from app.store.db import init_db, make_engine, make_session_factory
from app.store.models import OrderRow
from app.store.repos.order_repo import (STATUS_APPROVED, STATUS_CANCELLED,
                                        STATUS_FILLED, STATUS_SUBMITTED,
                                        create_order, get_order)
from app.store.repos.paper_repo import get_account, get_fills, get_positions, set_position
from app.store.repos.settings_repo import update_risk_params

D = dt.date(2026, 7, 17)
D1 = dt.date(2026, 7, 20)


@pytest.fixture
def engine():
    engine = make_engine(":memory:")
    init_db(engine)
    return engine


@pytest.fixture
def session(engine):
    with make_session_factory(engine)() as s:
        yield s


def _submitted(session, symbol="AAPL", side="buy", shares=10):
    row = create_order(session, D, symbol, side, shares, STATUS_APPROVED, "full_auto")
    return PaperBroker().submit(session, row)


def test_submit_marks_submitted_and_validates(session):
    row = _submitted(session)
    assert row.status == STATUS_SUBMITTED
    with pytest.raises(ValueError):
        PaperBroker().submit(session, OrderRow(as_of=D, symbol="X", side="hold",
                                               shares=1, status=STATUS_APPROVED, mode="full_auto"))
    with pytest.raises(ValueError):
        PaperBroker().submit(session, OrderRow(as_of=D, symbol="X", side="buy",
                                               shares=0, status=STATUS_APPROVED, mode="full_auto"))


def test_buy_fills_next_open_with_slippage(session):
    row = _submitted(session, shares=10)
    fills = PaperBroker(slippage_bps=100).process_fills(session, D1, {"AAPL": 100.0})
    session.commit()
    assert len(fills) == 1 and fills[0].shares == 10
    assert fills[0].price == pytest.approx(101.0)  # 1% 滑点
    assert get_order(session, row.id).status == STATUS_FILLED
    assert get_account(session, 100_000.0).cash == pytest.approx(100_000.0 - 1_010.0)
    position = get_positions(session)["AAPL"]
    assert position.shares == 10 and position.avg_cost == pytest.approx(101.0)


def test_buy_clamps_to_cash(session):
    update_risk_params(session, initial_cash=500.0)
    row = _submitted(session, shares=10)
    fills = PaperBroker(slippage_bps=100).process_fills(session, D1, {"AAPL": 100.0})
    assert fills[0].shares == 4  # int(500 // 101)
    assert get_order(session, row.id).status == STATUS_FILLED
    # Verify remaining cash: 500.0 - (4 * 101.0) = 96.0
    assert get_account(session, 500.0).cash == pytest.approx(96.0)


def test_buy_with_no_cash_cancelled_with_reason(session):
    update_risk_params(session, initial_cash=50.0)
    row = _submitted(session, shares=1)
    assert PaperBroker(slippage_bps=100).process_fills(session, D1, {"AAPL": 100.0}) == []
    out = get_order(session, row.id)
    assert out.status == STATUS_CANCELLED and "insufficient cash" in out.reason


def test_sell_clamps_to_position(session):
    set_position(session, "AAPL", 3, 90.0)
    _submitted(session, side="sell", shares=5)
    fills = PaperBroker(slippage_bps=0).process_fills(session, D1, {"AAPL": 110.0})
    assert fills[0].shares == 3  # 卖出按持仓截断
    assert get_positions(session) == {}
    assert get_account(session, 100_000.0).cash == pytest.approx(100_000.0 + 330.0)


def test_sell_without_position_cancelled(session):
    row = _submitted(session, side="sell", shares=5)
    assert PaperBroker().process_fills(session, D1, {"AAPL": 100.0}) == []
    out = get_order(session, row.id)
    assert out.status == STATUS_CANCELLED and "no position" in out.reason


def test_missing_open_price_cancelled_with_reason(session):
    row = _submitted(session)
    assert PaperBroker().process_fills(session, D1, {}) == []
    out = get_order(session, row.id)
    assert out.status == STATUS_CANCELLED and "no valid open price" in out.reason


def test_zero_price_cancels_and_batch_continues(session):
    """Test that 0-price orders are cancelled (not crash) and batch processing continues."""
    row_aaa = _submitted(session, symbol="AAA", side="buy", shares=10)
    row_bbb = _submitted(session, symbol="BBB", side="buy", shares=10)
    fills = PaperBroker().process_fills(session, D1, {"AAA": 0.0, "BBB": 100.0})
    session.commit()
    # AAA with 0 price should be cancelled
    order_aaa = get_order(session, row_aaa.id)
    assert order_aaa.status == STATUS_CANCELLED
    assert "no valid open price" in order_aaa.reason
    # BBB with valid price should be filled
    order_bbb = get_order(session, row_bbb.id)
    assert order_bbb.status == STATUS_FILLED
    # Verify fill happened for BBB
    assert len(fills) == 1 and fills[0].symbol == "BBB"


def test_avg_cost_recomputed_on_second_buy(session):
    """Test weighted average cost calculation with unequal share counts.

    Buy 1: 10 shares @ 100.0 = cost 1000
    Buy 2: 5 shares @ 120.0 = cost 600
    Total: 15 shares, total cost 1600, weighted avg = 1600/15 ≈ 106.67
    """
    set_position(session, "AAPL", 10, 100.0)
    _submitted(session, shares=5)  # Unequal to previous shares
    PaperBroker(slippage_bps=0).process_fills(session, D1, {"AAPL": 120.0})
    position = get_positions(session)["AAPL"]
    assert position.shares == 15
    expected_avg_cost = (10 * 100.0 + 5 * 120.0) / 15
    assert position.avg_cost == pytest.approx(expected_avg_cost)


def test_state_survives_restart(engine):
    with make_session_factory(engine)() as session:
        _submitted(session, shares=10)
        PaperBroker(slippage_bps=0).process_fills(session, D1, {"AAPL": 100.0})
        session.commit()
    with make_session_factory(engine)() as session:  # 模拟重启
        assert get_positions(session)["AAPL"].shares == 10
        assert get_account(session, 100_000.0).cash == pytest.approx(99_000.0)
        assert len(get_fills(session)) == 1
