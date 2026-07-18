import datetime as dt

import pandas as pd
import pytest

from app.backtest.metrics import max_drawdown, round_trips, sharpe, total_return, win_rate
from app.backtest.sim_broker import Fill

D = dt.date(2024, 1, 2)


def _fill(side, shares, price, symbol="AAA"):
    return Fill(D, symbol, side, shares, price)


def test_total_return():
    assert total_return(pd.Series([100.0, 110.0, 121.0])) == pytest.approx(0.21)


def test_max_drawdown():
    eq = pd.Series([100.0, 120.0, 90.0, 130.0])
    assert max_drawdown(eq) == pytest.approx(-0.25)


def test_sharpe_zero_for_flat_curve():
    assert sharpe(pd.Series([100.0] * 10)) == 0.0
    assert sharpe(pd.Series([100.0])) == 0.0


def test_sharpe_positive_for_rising_curve():
    eq = pd.Series([100.0 * (1.01 ** i) + (i % 2) for i in range(50)])
    assert sharpe(eq) > 0


def test_round_trips_fifo_partial():
    fills = [
        _fill("buy", 10, 100.0),
        _fill("buy", 10, 110.0),
        _fill("sell", 15, 120.0),
    ]
    assert round_trips(fills) == [pytest.approx(10 * 20.0 + 5 * 10.0)]


def test_win_rate():
    fills = [
        _fill("buy", 10, 100.0),
        _fill("sell", 10, 110.0),  # win
        _fill("buy", 10, 100.0, symbol="BBB"),
        _fill("sell", 10, 90.0, symbol="BBB"),  # loss
    ]
    assert win_rate(fills) == pytest.approx(0.5)
    assert win_rate([]) == 0.0
