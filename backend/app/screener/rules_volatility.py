import math

import pandas as pd

from app.screener.base import Rule, RuleResult, clamp01
from app.screener.indicators import atr

MIN_BARS = 30

# ATR% 映射边界:<=1.5% 视为"平静"→满分,>=5.0% 视为"剧烈波动"→零分,区间内线性。
# 这两个阈值是大盘蓝筹股日线 ATR(14)/close 的粗粒度经验区间,不是从本仓库数据拟合出来的。
LOW = 0.015
HIGH = 0.05


class VolatilityRule(Rule):
    """波动率(候选因子,未接入 default_screener):ATR(14)/close 越低分越高,
    偏好走势平稳、回撤空间小的标的,用于压低组合最大回撤。"""

    name = "volatility"

    def evaluate(self, bars: pd.DataFrame) -> RuleResult:
        if len(bars) < MIN_BARS:
            return RuleResult(0.0, f"insufficient data ({len(bars)} bars)")
        close = bars["close"]
        last_close = close.iloc[-1]
        atr14 = atr(bars, 14).iloc[-1]
        if math.isnan(last_close) or math.isnan(atr14) or last_close <= 0:
            return RuleResult(0.0, "nan inputs")
        atr_pct = atr14 / last_close
        score = clamp01((HIGH - atr_pct) / (HIGH - LOW))
        return RuleResult(score, f"atr%={atr_pct:.2%}")
