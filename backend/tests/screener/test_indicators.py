import math

import pandas as pd
import pytest

from app.screener.indicators import atr, ema, pct_return, rsi, sma, true_range
from tests.helpers import make_bars


def test_sma_known_values():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    out = sma(s, 3)
    assert math.isnan(out.iloc[1])
    assert out.iloc[2] == pytest.approx(2.0)
    assert out.iloc[4] == pytest.approx(4.0)


def test_ema_warmup_nan_then_value():
    s = pd.Series([1.0] * 10)
    out = ema(s, 5)
    assert math.isnan(out.iloc[3])
    assert out.iloc[-1] == pytest.approx(1.0)


def test_rsi_all_gains_near_100():
    s = pd.Series(range(1, 40), dtype=float)
    assert rsi(s).iloc[-1] > 95


def test_rsi_all_losses_near_0():
    s = pd.Series(range(40, 1, -1), dtype=float)
    assert rsi(s).iloc[-1] < 5


def test_rsi_bounded_on_mixed_series():
    s = pd.Series([100 + (i % 5) - 2 for i in range(60)], dtype=float)
    tail = rsi(s).dropna()
    assert ((tail >= 0) & (tail <= 100)).all()


def test_true_range_and_atr_positive():
    bars = make_bars(days=30)
    assert (true_range(bars).dropna() > 0).all()
    assert atr(bars).dropna().iloc[-1] > 0


def test_pct_return():
    s = pd.Series([100.0, 110.0, 121.0])
    assert pct_return(s, 1).iloc[-1] == pytest.approx(0.1)
    assert pct_return(s, 2).iloc[-1] == pytest.approx(0.21)
