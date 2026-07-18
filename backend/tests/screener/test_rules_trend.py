import pytest

from app.screener.rules_trend import TrendRule
from tests.helpers import make_bars


def test_uptrend_scores_full():
    bars = make_bars(days=120, base=100.0, step=1.0)
    out = TrendRule().evaluate(bars)
    assert out.score == pytest.approx(1.0)


def test_downtrend_scores_zero():
    bars = make_bars(days=120, base=500.0, step=-1.0)
    out = TrendRule().evaluate(bars)
    assert out.score == pytest.approx(0.0)


def test_insufficient_data_scores_zero():
    out = TrendRule().evaluate(make_bars(days=30))
    assert out.score == 0.0
    assert "insufficient" in out.detail


def test_nan_close_scores_zero():
    """≥60 根 K 线但 close 里混入 NaN(如停牌/数据缺失日)不得让指标悄悄算出脏分数。"""
    bars = make_bars(days=61, base=100.0, step=1.0)
    bars.iloc[-1, bars.columns.get_loc("close")] = float("nan")
    out = TrendRule().evaluate(bars)
    assert out.score == 0.0
    assert out.detail == "insufficient data (nan sma)"
