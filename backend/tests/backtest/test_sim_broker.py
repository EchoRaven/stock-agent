import datetime as dt

import pytest

from app.backtest.sim_broker import Fill, Order, SimBroker

D = dt.date(2024, 1, 2)


def test_buy_fills_at_open_with_slippage():
    b = SimBroker(cash=10_000, slippage_bps=100)  # 1%
    b.submit(Order("AAPL", "buy", 10))
    fills = b.process_fills(D, {"AAPL": 100.0})
    assert fills == [Fill(D, "AAPL", "buy", 10, pytest.approx(101.0))]
    assert b.cash == pytest.approx(10_000 - 1010.0)
    assert b.position("AAPL") == 10


def test_buy_clamps_to_affordable_shares():
    b = SimBroker(cash=500, slippage_bps=100)
    b.submit(Order("AAPL", "buy", 10))
    fills = b.process_fills(D, {"AAPL": 100.0})
    assert fills[0].shares == 4  # int(500 // 101)
    assert b.cash == pytest.approx(500 - 4 * 101.0)


def test_sell_clamps_to_position():
    b = SimBroker(cash=10_000, slippage_bps=0)
    b.submit(Order("AAPL", "buy", 3))
    b.process_fills(D, {"AAPL": 100.0})
    b.submit(Order("AAPL", "sell", 5))
    fills = b.process_fills(dt.date(2024, 1, 3), {"AAPL": 110.0})
    assert fills[0].shares == 3
    assert b.position("AAPL") == 0
    assert b.cash == pytest.approx(10_000 - 300 + 330)


def test_sell_without_position_is_dropped():
    b = SimBroker(cash=1_000)
    b.submit(Order("AAPL", "sell", 5))
    assert b.process_fills(D, {"AAPL": 100.0}) == []


def test_missing_open_price_drops_order():
    b = SimBroker(cash=1_000)
    b.submit(Order("AAPL", "buy", 1))
    assert b.process_fills(D, {}) == []
    assert b.process_fills(dt.date(2024, 1, 3), {"AAPL": 100.0}) == []  # 挂单不跨日残留


def test_equity_and_missing_close_raises():
    b = SimBroker(cash=10_000, slippage_bps=0)
    b.submit(Order("AAPL", "buy", 10))
    b.process_fills(D, {"AAPL": 100.0})
    assert b.equity({"AAPL": 110.0}) == pytest.approx(9_000 + 1_100)
    with pytest.raises(KeyError):
        b.equity({})


def test_submit_validation():
    b = SimBroker(cash=1_000)
    with pytest.raises(ValueError):
        b.submit(Order("AAPL", "hold", 1))
    with pytest.raises(ValueError):
        b.submit(Order("AAPL", "buy", 0))
    with pytest.raises(ValueError):
        SimBroker(cash=0)
