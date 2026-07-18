import pandas as pd

from app.screener.base import Rule, RuleResult, clamp01

MIN_BARS = 60


class VolumeRule(Rule):
    """量能:近5日均量/近60日均量,0.5→0 分,2.0→1 分,线性映射。"""

    name = "volume"

    def evaluate(self, bars: pd.DataFrame) -> RuleResult:
        if len(bars) < MIN_BARS:
            return RuleResult(0.0, f"insufficient data ({len(bars)} bars)")
        v5 = float(bars["volume"].iloc[-5:].mean())
        v60 = float(bars["volume"].iloc[-60:].mean())
        if v60 <= 0:
            return RuleResult(0.0, "no volume")
        ratio = v5 / v60
        return RuleResult(clamp01((ratio - 0.5) / 1.5), f"vol5/vol60={ratio:.2f}")
