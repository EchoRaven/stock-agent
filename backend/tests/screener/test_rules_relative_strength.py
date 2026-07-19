import pandas as pd
import pytest

from app.screener.base import clamp01
from app.screener.indicators import pct_return
from app.screener.rules_relative_strength import RelativeStrengthRule
from tests.helpers import make_bars


def _with_benchmark(bars: pd.DataFrame, bench_close: pd.Series) -> pd.DataFrame:
    out = bars.copy()
    out["benchmark_close"] = bench_close.reindex(out.index)
    return out


def test_missing_benchmark_column_scores_zero():
    out = RelativeStrengthRule().evaluate(make_bars(days=30, base=100.0, step=1.0))
    assert out.score == 0.0
    assert "no benchmark" in out.detail


def test_insufficient_data_scores_zero():
    bars = make_bars(days=10, base=100.0, step=1.0)
    bars = _with_benchmark(bars, pd.Series(50.0, index=bars.index))
    out = RelativeStrengthRule().evaluate(bars)
    assert out.score == 0.0
    assert "insufficient" in out.detail


def test_outperformance_scores_full():
    """个股上涨、基准走平 → 超额收益远超 +10% 上限 → 满分。"""
    bars = make_bars(days=30, base=100.0, step=1.0)
    bars = _with_benchmark(bars, pd.Series(50.0, index=bars.index))
    out = RelativeStrengthRule().evaluate(bars)
    assert out.score == pytest.approx(1.0)


def test_underperformance_scores_zero():
    """个股走平、基准大涨 → 超额收益远低于 -10% 下限 → 零分。"""
    bars = make_bars(days=30, base=100.0, step=0.0)
    bench = make_bars(days=30, base=50.0, step=1.0)["close"]
    bars = _with_benchmark(bars, bench)
    out = RelativeStrengthRule().evaluate(bars)
    assert out.score == pytest.approx(0.0)


def test_exact_score_matches_formula():
    bars = make_bars(days=30, base=100.0, step=0.2)
    bench = make_bars(days=30, base=50.0, step=0.05)["close"]
    merged = _with_benchmark(bars, bench)
    out = RelativeStrengthRule().evaluate(merged)
    stock_ret = pct_return(merged["close"], 20).iloc[-1]
    bench_ret = pct_return(merged["benchmark_close"], 20).iloc[-1]
    expected = clamp01((stock_ret - bench_ret + 0.10) / 0.20)
    assert out.score == pytest.approx(expected)


def test_nan_benchmark_scores_zero():
    bars = make_bars(days=30, base=100.0, step=1.0)
    bench = pd.Series(50.0, index=bars.index)
    bench.iloc[-1] = float("nan")
    bars = _with_benchmark(bars, bench)
    out = RelativeStrengthRule().evaluate(bars)
    assert out.score == 0.0
    assert out.detail == "nan inputs"
