"""Phase 4:安全因子目录——LLM 唯一能"提案"的表面。

HARD SAFETY RULE:LLM 从不产出/提交任何代码或表达式,只产出结构化数据
{"factor": "<目录内名字>", "params": {...整数...}}。本文件里手写、受审查的
Rule 工厂是全系统唯一真正被执行的代码;`validate_params`/`build_factor`
把任何目录之外的名字、任何非 int/越界的参数当纯垃圾数据拒绝——即使一条
提案里塞进 "__import__"、"; rm -rf" 这类字符串,也只是被判 False/raise
ValueError 的普通数据,永远不会被 eval/exec,也不会传给下面的工厂函数。
"""
import math
from dataclasses import dataclass
from typing import Callable

import pandas as pd

from app.screener.base import Rule, RuleResult, clamp01
from app.screener.indicators import pct_return, rsi, sma


# ---------------------------------------------------------------------------
# 五个手写、受审查的规则工厂。公式固定,只有整数参数在校验通过的范围内变化。
# ---------------------------------------------------------------------------


class _MomentumRule(Rule):
    """momentum(window):N 日收益率,-10% -> 0,+30% -> 1 线性映射。"""

    def __init__(self, params: dict):
        self._window = params["window"]
        self.name = f"momentum({params})"

    def evaluate(self, bars: pd.DataFrame) -> RuleResult:
        window = self._window
        if len(bars) < window + 1:
            return RuleResult(0.0, f"insufficient data ({len(bars)} bars)")
        ret = pct_return(bars["close"], window).iloc[-1]
        if math.isnan(ret):
            return RuleResult(0.0, "nan input")
        return RuleResult(clamp01((ret + 0.10) / 0.40), f"ret{window}={ret:.2%}")


class _LowVolatilityRule(Rule):
    """low_volatility(window):近 window 日收益率标准差,越低分越高。"""

    def __init__(self, params: dict):
        self._window = params["window"]
        self.name = f"low_volatility({params})"

    def evaluate(self, bars: pd.DataFrame) -> RuleResult:
        window = self._window
        if len(bars) < window + 1:
            return RuleResult(0.0, f"insufficient data ({len(bars)} bars)")
        vol = bars["close"].pct_change().iloc[-window:].std()
        if vol is None or math.isnan(vol):
            return RuleResult(0.0, "nan input")
        return RuleResult(clamp01((0.04 - vol) / (0.04 - 0.005)), f"vol{window}={vol:.4f}")


class _RsiStrengthRule(Rule):
    """rsi_strength(window):RSI 40 -> 0,70 -> 1 线性映射。"""

    def __init__(self, params: dict):
        self._window = params["window"]
        self.name = f"rsi_strength({params})"

    def evaluate(self, bars: pd.DataFrame) -> RuleResult:
        window = self._window
        if len(bars) < window + 1:
            return RuleResult(0.0, f"insufficient data ({len(bars)} bars)")
        r = rsi(bars["close"], window).iloc[-1]
        if math.isnan(r):
            return RuleResult(0.0, "nan input")
        return RuleResult(clamp01((r - 40) / 30), f"rsi{window}={r:.1f}")


class _SmaTrendRule(Rule):
    """sma_trend(fast<slow):[close>sma(fast), sma(fast)>sma(slow)] 均值 -> 0/0.5/1。"""

    def __init__(self, params: dict):
        self._fast = params["fast"]
        self._slow = params["slow"]
        self.name = f"sma_trend({params})"

    def evaluate(self, bars: pd.DataFrame) -> RuleResult:
        fast, slow = self._fast, self._slow
        if len(bars) < slow:
            return RuleResult(0.0, f"insufficient data ({len(bars)} bars)")
        close = bars["close"]
        last = close.iloc[-1]
        sma_fast = sma(close, fast).iloc[-1]
        sma_slow = sma(close, slow).iloc[-1]
        if any(math.isnan(v) for v in (last, sma_fast, sma_slow)):
            return RuleResult(0.0, "nan input")
        checks = [last > sma_fast, sma_fast > sma_slow]
        score = sum(checks) / len(checks)
        return RuleResult(score, f"close>sma{fast}={checks[0]}, sma{fast}>sma{slow}={checks[1]}")


class _VolumeTrendRule(Rule):
    """volume_trend(window):近5日均量/近window日均量,0.8 -> 0,1.5 -> 1。"""

    def __init__(self, params: dict):
        self._window = params["window"]
        self.name = f"volume_trend({params})"

    def evaluate(self, bars: pd.DataFrame) -> RuleResult:
        window = self._window
        if len(bars) < window:
            return RuleResult(0.0, f"insufficient data ({len(bars)} bars)")
        v5 = bars["volume"].iloc[-5:].mean()
        vw = bars["volume"].iloc[-window:].mean()
        if vw is None or math.isnan(vw) or vw <= 0 or math.isnan(v5):
            return RuleResult(0.0, "no volume")
        ratio = v5 / vw
        return RuleResult(clamp01((ratio - 0.8) / 0.7), f"vol5/vol{window}={ratio:.2f}")


@dataclass(frozen=True)
class FactorSpec:
    params: dict  # param name -> (min, max) 闭区间整数边界
    build: Callable[[dict], Rule]
    description: str


FACTORS: dict[str, FactorSpec] = {
    "momentum": FactorSpec(
        params={"window": (10, 120)},
        build=lambda p: _MomentumRule(p),
        description="N 日收益率动量:-10% -> 0 分,+30% -> 1 分线性映射。",
    ),
    "low_volatility": FactorSpec(
        params={"window": (10, 60)},
        build=lambda p: _LowVolatilityRule(p),
        description="近 N 日收益率标准差(波动率),低波动 -> 高分。",
    ),
    "rsi_strength": FactorSpec(
        params={"window": (5, 30)},
        build=lambda p: _RsiStrengthRule(p),
        description="N 日 RSI:40 -> 0 分,70 -> 1 分线性映射。",
    ),
    "sma_trend": FactorSpec(
        params={"fast": (5, 50), "slow": (20, 200)},
        build=lambda p: _SmaTrendRule(p),
        description="快/慢均线排列(要求 fast < slow):收盘>快线、快线>慢线各占一半。",
    ),
    "volume_trend": FactorSpec(
        params={"window": (10, 60)},
        build=lambda p: _VolumeTrendRule(p),
        description="近 5 日均量 / 近 N 日均量:0.8 -> 0 分,1.5 -> 1 分线性映射。",
    ),
}


def validate_params(name, params) -> tuple:
    """校验 (name, params) 是否是目录内的合法提案。绝不执行/构造任何东西——
    纯数据校验,任何目录之外的名字或非 int/越界参数都在这里被当垃圾数据拒绝。
    """
    if not isinstance(name, str) or name not in FACTORS:
        return False, f"unknown factor {name!r}"
    spec = FACTORS[name]
    if not isinstance(params, dict):
        return False, "params must be a dict"
    if set(params.keys()) != set(spec.params.keys()):
        return False, f"params keys must be exactly {sorted(spec.params.keys())}"
    for key, (lo, hi) in spec.params.items():
        value = params[key]
        if isinstance(value, bool) or not isinstance(value, int):
            return False, f"{key} must be an int"
        if not (lo <= value <= hi):
            return False, f"{key} must be in [{lo}, {hi}]"
    if name == "sma_trend" and params["fast"] >= params["slow"]:
        return False, "fast must be < slow"
    return True, ""


def build_factor(name, params) -> Rule:
    """校验通过才调用手写工厂;非法输入 raise ValueError,工厂函数绝不会被
    目录之外的名字或垃圾参数触达。"""
    ok, err = validate_params(name, params)
    if not ok:
        raise ValueError(f"invalid factor proposal: {err}")
    return FACTORS[name].build(dict(params))


def catalog_for_prompt() -> str:
    """给 LLM 提案 prompt 用的紧凑目录说明(名字 + 参数边界),人类也能读。"""
    lines = ["可选因子目录(只能从下列 factor 名称 + 括注范围内的整数参数中选择,"
            "不接受任何代码/表达式/目录之外的名字):"]
    for name, spec in FACTORS.items():
        bounds = ", ".join(f"{k}:[{lo},{hi}]" for k, (lo, hi) in spec.params.items())
        lines.append(f"- {name}({bounds}): {spec.description}")
    return "\n".join(lines)
