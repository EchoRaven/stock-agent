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
