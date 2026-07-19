import pytest

from app.screener.rules_volatility import VolatilityRule
from tests.helpers import make_bars


def test_calm_stock_scores_full():
    """高价、固定±1 波幅 → ATR/close 极小(≈0.2%,低于 1.5% 下限)→ 满分。"""
    bars = make_bars(days=40, base=1000.0, step=0.0)
    out = VolatilityRule().evaluate(bars)
    assert out.score == pytest.approx(1.0)


def test_volatile_stock_scores_zero():
    """低价、同样固定±1 波幅 → ATR/close 远超 5% 上限 → 零分。"""
    bars = make_bars(days=40, base=25.0, step=0.0)
    out = VolatilityRule().evaluate(bars)
    assert out.score == pytest.approx(0.0)


def test_calm_beats_volatile():
    calm = VolatilityRule().evaluate(make_bars(days=40, base=1000.0, step=0.0))
    volatile = VolatilityRule().evaluate(make_bars(days=40, base=25.0, step=0.0))
    assert calm.score > volatile.score


def test_insufficient_data_scores_zero():
    out = VolatilityRule().evaluate(make_bars(days=10))
    assert out.score == 0.0
    assert "insufficient" in out.detail


def test_nan_close_scores_zero():
    bars = make_bars(days=40, base=100.0, step=0.0)
    bars.iloc[-1, bars.columns.get_loc("close")] = float("nan")
    out = VolatilityRule().evaluate(bars)
    assert out.score == 0.0
    assert out.detail == "nan inputs"
