import math

import pandas as pd

from app.screener.base import Rule, RuleResult, clamp01
from app.screener.indicators import pct_return

MIN_BARS = 30
PERIOD = 20
# 超额收益 -10%~+10% 线性映射到 0~1(经验区间,非拟合结果)。
BAND = 0.10


class RelativeStrengthRule(Rule):
    """相对强度(候选因子,未接入 default_screener):
    个股 N 日收益 - 基准(如 SPY)同期 N 日收益。

    依赖 bars 里一列 'benchmark_close'——由调用方按日期对齐把基准收盘价合并进每只
    标的自己的 bars DataFrame(而不是在 Rule 里另开一路取数),这样回测引擎按
    as-of 日期切片时基准数据和标的数据天然同步截断,不会引入未来函数。
    default_screener() 产出的 bars 没有这一列,所以这条规则对现有默认流程永远是
    0 分、无副作用。"""

    name = "relative_strength"

    def evaluate(self, bars: pd.DataFrame) -> RuleResult:
        if len(bars) < MIN_BARS or "benchmark_close" not in bars.columns:
            return RuleResult(0.0, "insufficient data (no benchmark)")
        stock_ret = pct_return(bars["close"], PERIOD).iloc[-1]
        bench_ret = pct_return(bars["benchmark_close"], PERIOD).iloc[-1]
        if math.isnan(stock_ret) or math.isnan(bench_ret):
            return RuleResult(0.0, "nan inputs")
        excess = stock_ret - bench_ret
        score = clamp01((excess + BAND) / (2 * BAND))
        return RuleResult(score, f"excess{PERIOD}={excess:.2%}")
