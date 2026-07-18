import numpy as np
import pandas as pd


def make_bars(start="2024-01-01", days=10, base=100.0, step=1.0, volume=1_000_000):
    """构造合成日线:close = base + step*i,open=close-0.5,high/low=close±1。"""
    idx = pd.bdate_range(start, periods=days)
    close = pd.Series(base + step * np.arange(days, dtype=float), index=idx)
    return pd.DataFrame(
        {
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": float(volume),
        },
        index=idx,
    )


def make_decision_payload(**overrides):
    """合法的委员会决定 payload(建议模式);字段可用 overrides 覆盖。"""
    payload = {
        "symbol": "AAPL",
        "as_of": "2026-07-17",
        "action": "buy",
        "confidence": 0.8,
        "shares": 10,
        "committee": {
            "technical": {"summary": "多头排列,站上 SMA20"},
            "fundamental": {"summary": "营收与 EPS 连续增长"},
            "sentiment": {"summary": "新闻面偏多"},
            "bear": {"summary": "短期涨幅过大,存在回调风险"},
        },
        "chair": {"verdict": "小仓位买入", "bear_rebuttal": "回调风险由小仓位与止损覆盖"},
    }
    payload.update(overrides)
    return payload
