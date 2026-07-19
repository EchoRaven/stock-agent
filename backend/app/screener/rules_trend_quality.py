import math

import pandas as pd

from app.screener.base import Rule, RuleResult, clamp01
from app.screener.indicators import sma

MIN_BARS = 61
UP_DAY_WINDOW = 60
SMA_WINDOW = 20
DEV_WINDOW = 20
# 均值偏离 >= 5% 记 0 分,0% 记满分,区间内线性(经验阈值,非拟合结果)。
MAX_DEV = 0.05


class TrendQualityRule(Rule):
    """趋势质量(候选因子,未接入 default_screener):
    0.5×上涨天数占比(近60日) + 0.5×价格贴合SMA20 程度(近20日均相对偏离越小分越高)。
    目的是压低"暴涨暴跌拼出来的上涨"的分数,偏好走势平滑的趋势,用于降低回撤。"""

    name = "trend_quality"

    def evaluate(self, bars: pd.DataFrame) -> RuleResult:
        if len(bars) < MIN_BARS:
            return RuleResult(0.0, f"insufficient data ({len(bars)} bars)")
        close = bars["close"]
        diffs = close.diff().iloc[-UP_DAY_WINDOW:]
        if diffs.isna().any():
            return RuleResult(0.0, "insufficient data (nan diff)")
        up_frac = float((diffs > 0).mean())

        s20 = sma(close, SMA_WINDOW)
        recent_close = close.iloc[-DEV_WINDOW:]
        recent_sma = s20.iloc[-DEV_WINDOW:]
        if recent_sma.isna().any():
            return RuleResult(0.0, "insufficient data (nan sma)")
        deviation = float(((recent_close - recent_sma).abs() / recent_sma).mean())
        if math.isnan(deviation):
            return RuleResult(0.0, "nan inputs")

        smooth_score = clamp01(1 - deviation / MAX_DEV)
        score = 0.5 * up_frac + 0.5 * smooth_score
        return RuleResult(score, f"up_frac={up_frac:.2f}, avg_dev={deviation:.2%}")
