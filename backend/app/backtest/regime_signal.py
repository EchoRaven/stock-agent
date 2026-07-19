"""市场状态(regime)信号:用大盘指数(SPY)相对其 N 日均线判断风险开关。
防未来函数:as-of 某日只用 index 价格 <= 该日的数据。"""
import datetime as dt
import pandas as pd


def risk_on_asof(index_bars: pd.DataFrame, as_of: dt.date, sma_window: int = 200) -> bool:
    """SPY as_of 日收盘 > 其 sma_window 日均线 → 风险开(True),否则 False。
    历史不足 sma_window 天(均线未定义)→ 默认 True(不因数据不足而误判防御)。
    index_bars: 含 'close' 列、DatetimeIndex 的 SPY 日线。绝不抛异常(缺数据→True)。"""
    try:
        if index_bars is None or "close" not in index_bars or index_bars.empty:
            return True
        # 只取 index 日期 <= as_of 的收盘(防未来函数)
        closes = index_bars["close"][index_bars.index.date <= as_of]
        if len(closes) < sma_window:
            return True
        sma = closes.iloc[-sma_window:].mean()
        last = closes.iloc[-1]
        if pd.isna(sma) or pd.isna(last):
            return True
        return bool(last > sma)
    except Exception:
        return True  # 任何异常都不能拖垮回测;保守回退到 risk-on(=不额外干预)
