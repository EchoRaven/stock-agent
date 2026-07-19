import pandas as pd
import pytest

from app.screener.rules_trend_quality import TrendQualityRule
from tests.helpers import make_bars


def _choppy_bars(days=61, base=1000.0, up=5.0, down=3.0):
    """交替 +up/-down 拼出来的"上涨"(净漂移与平滑趋势相近,但天天反复)。"""
    idx = pd.bdate_range("2024-01-01", periods=days)
    vals = []
    x = base
    for i in range(days):
        x += up if i % 2 == 0 else -down
        vals.append(x)
    close = pd.Series(vals, index=idx)
    return pd.DataFrame(
        {
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1_000_000.0,
        },
        index=idx,
    )


def test_smooth_uptrend_scores_high():
    bars = make_bars(days=61, base=1000.0, step=1.0)
    out = TrendQualityRule().evaluate(bars)
    assert out.score > 0.85


def test_choppy_uptrend_scores_lower_than_smooth():
    """同量级净涨幅,但天天反复的走势应该比平滑上涨分低(靠上涨天数占比拉开差距)。"""
    smooth = TrendQualityRule().evaluate(make_bars(days=61, base=1000.0, step=1.0))
    choppy = TrendQualityRule().evaluate(_choppy_bars())
    assert choppy.score < smooth.score
    assert "up_frac=0.50" in choppy.detail


def test_downtrend_scores_lower_than_uptrend():
    """0 上涨天数只拖累一半的分(另一半是均线贴合度,涨跌趋势本身都很平滑),
    所以只断言"明显更低",不断言接近 0。"""
    up = TrendQualityRule().evaluate(make_bars(days=61, base=1000.0, step=1.0))
    down = TrendQualityRule().evaluate(make_bars(days=61, base=1000.0, step=-1.0))
    assert down.score < 0.5
    assert down.score < up.score


def test_insufficient_data_scores_zero():
    out = TrendQualityRule().evaluate(make_bars(days=30))
    assert out.score == 0.0
    assert "insufficient" in out.detail


def test_nan_close_scores_zero():
    bars = make_bars(days=61, base=1000.0, step=1.0)
    bars.iloc[-1, bars.columns.get_loc("close")] = float("nan")
    out = TrendQualityRule().evaluate(bars)
    assert out.score == 0.0
    assert "insufficient" in out.detail
