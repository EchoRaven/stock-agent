"""risk_on_asof() 纯函数单测:防未来函数 + 边界/异常兜底(见 regime_signal.py)。"""
import datetime as dt

import numpy as np
import pandas as pd

from app.backtest.regime_signal import risk_on_asof


def _bars_from_closes(closes, start="2024-01-01"):
    """构造自定义收盘价路径的最小日线(只需要 'close' 列)。"""
    idx = pd.bdate_range(start, periods=len(closes))
    return pd.DataFrame({"close": [float(c) for c in closes]}, index=idx)


def test_risk_on_when_above_sma():
    # 单调上涨:最后一日收盘必然高于其 SMA(均线滞后于上涨趋势)。
    closes = [100.0 + i for i in range(250)]
    bars = _bars_from_closes(closes)
    as_of = bars.index[-1].date()
    assert risk_on_asof(bars, as_of, sma_window=200) is True


def test_risk_off_when_below_sma():
    # 单调下跌:最后一日收盘必然低于其 SMA。
    closes = [500.0 - i for i in range(250)]
    bars = _bars_from_closes(closes)
    as_of = bars.index[-1].date()
    assert risk_on_asof(bars, as_of, sma_window=200) is False


def test_insufficient_history_defaults_risk_on():
    # 历史只有 50 天 < sma_window=200 -> 均线未定义 -> 默认 risk-on。
    closes = [500.0 - i for i in range(50)]  # 即使趋势向下也应默认 True
    bars = _bars_from_closes(closes)
    as_of = bars.index[-1].date()
    assert risk_on_asof(bars, as_of, sma_window=200) is True


def test_lookahead_safe_ignores_future():
    # as_of 之前:先涨后小幅回落,构造出一个 <=as_of 时明确 risk-off 的局面。
    # as_of 之后:插入一次剧烈崩盘(远低于任何合理均线),这必须被完全忽略。
    pre = [100.0 + i for i in range(220)]  # 上涨到 319
    pre += [319.0 - i for i in range(30)]  # 回落到 290,让最后收盘略低于其 SMA200
    bars_index = pd.bdate_range("2024-01-01", periods=len(pre))
    as_of = bars_index[-1].date()

    # 手工计算 <=as_of 切片下的期望结果
    closes_upto = pd.Series(pre, index=bars_index)
    sma200 = closes_upto.iloc[-200:].mean()
    expected = bool(closes_upto.iloc[-1] > sma200)

    # future crash 数据接在 as_of 之后(日期严格晚于 as_of)
    future = [1.0, 0.5, 0.1, 0.01] * 10  # 崩盘到接近 0
    full_index = pd.bdate_range("2024-01-01", periods=len(pre) + len(future))
    full_closes = pre + future
    bars = pd.DataFrame({"close": full_closes}, index=full_index)

    assert bars.index[-1].date() > as_of  # 确认未来数据确实晚于 as_of
    actual = risk_on_asof(bars, as_of, sma_window=200)
    assert actual == expected

    # 再次断言:把未来数据换成截然相反的方向(暴涨而非崩盘),结果必须不变。
    future_up = [1000.0 + i * 500 for i in range(len(future))]
    bars_up = pd.DataFrame({"close": pre + future_up}, index=full_index)
    assert risk_on_asof(bars_up, as_of, sma_window=200) == expected


def test_empty_or_missing_close_returns_true():
    assert risk_on_asof(pd.DataFrame(), dt.date(2024, 1, 1)) is True
    assert risk_on_asof(pd.DataFrame({"open": [1.0, 2.0]}), dt.date(2024, 1, 1)) is True
    assert risk_on_asof(None, dt.date(2024, 1, 1)) is True


def test_never_raises():
    # 各种垃圾输入都不应抛异常,一律兜底 True。
    assert risk_on_asof("not a dataframe", dt.date(2024, 1, 1)) is True
    assert risk_on_asof(123, dt.date(2024, 1, 1)) is True
    assert risk_on_asof(pd.DataFrame({"close": [np.nan, np.nan]},
                                      index=pd.bdate_range("2024-01-01", periods=2)),
                         dt.date(2024, 1, 5), sma_window=1) is True
    assert risk_on_asof(object(), dt.date(2024, 1, 1)) is True
