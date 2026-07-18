import math

import pandas as pd

from app.screener.base import Rule, RuleResult, clamp01
from app.screener.indicators import pct_return, rsi

MIN_BARS = 30


def rsi_band_score(value: float) -> float:
    """RSI 健康区间打分:<30→0,30-50 线性升,50-70→1,70-80 线性降,>80→0(过热)。"""
    if value < 30:
        return 0.0
    if value < 50:
        return (value - 30) / 20
    if value <= 70:
        return 1.0
    if value <= 80:
        return (80 - value) / 10
    return 0.0


class MomentumRule(Rule):
    """动量:0.6×20日收益(-10%~+20% 线性映射到 0~1) + 0.4×RSI 区间分。"""

    name = "momentum"

    def evaluate(self, bars: pd.DataFrame) -> RuleResult:
        if len(bars) < MIN_BARS:
            return RuleResult(0.0, f"insufficient data ({len(bars)} bars)")
        ret20 = pct_return(bars["close"], 20).iloc[-1]
        rsi14 = rsi(bars["close"], 14).iloc[-1]
        if math.isnan(ret20) or math.isnan(rsi14):
            return RuleResult(0.0, "nan inputs")
        ret_score = clamp01((ret20 + 0.10) / 0.30)
        score = 0.6 * ret_score + 0.4 * rsi_band_score(rsi14)
        return RuleResult(score, f"ret20={ret20:.2%}, rsi14={rsi14:.1f}")
