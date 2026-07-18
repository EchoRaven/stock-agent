import math

import pandas as pd

from app.screener.base import Rule, RuleResult
from app.screener.indicators import sma

MIN_BARS = 60


class TrendRule(Rule):
    """趋势:收盘>SMA20、SMA20>SMA50、SMA50 走高(对比 5 日前),各占 1/3。"""

    name = "trend"

    def evaluate(self, bars: pd.DataFrame) -> RuleResult:
        if len(bars) < MIN_BARS:
            return RuleResult(0.0, f"insufficient data ({len(bars)} bars)")
        close = bars["close"]
        s20 = sma(close, 20)
        s50 = sma(close, 50)
        values = [close.iloc[-1], s20.iloc[-1], s50.iloc[-1], s50.iloc[-6]]
        if any(math.isnan(v) for v in values):
            return RuleResult(0.0, "insufficient data (nan sma)")
        checks = {
            "close>sma20": values[0] > values[1],
            "sma20>sma50": values[1] > values[2],
            "sma50 rising": values[2] > values[3],
        }
        score = sum(checks.values()) / len(checks)
        detail = ", ".join(f"{k}={v}" for k, v in checks.items())
        return RuleResult(score, detail)
