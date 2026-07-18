import math
from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd


def clamp01(x: float) -> float:
    """把分数夹到 [0,1]。NaN 视为 0,防止一个 NaN 规则分污染总分与排序。"""
    x = float(x)
    if math.isnan(x):
        return 0.0
    return min(max(x, 0.0), 1.0)


@dataclass(frozen=True)
class RuleResult:
    score: float  # 0.0 - 1.0
    detail: str


class Rule(ABC):
    name: str = "rule"

    @abstractmethod
    def evaluate(self, bars: pd.DataFrame) -> RuleResult:
        """对单只标的的日线历史打分。数据不足时返回 score=0。"""


@dataclass(frozen=True)
class SymbolScore:
    symbol: str
    total: float  # 加权总分 0.0 - 1.0
    parts: dict  # rule name -> RuleResult


class Screener:
    """按 (Rule, weight) 组合打分并排序。规则抛异常按 0 分记,不中断整轮筛选。"""

    def __init__(self, weighted_rules: list):
        if not weighted_rules:
            raise ValueError("weighted_rules must not be empty")
        self._weight_sum = sum(w for _, w in weighted_rules)
        if self._weight_sum <= 0:
            raise ValueError("rule weights must sum to a positive number")
        self._rules = weighted_rules

    def score_symbol(self, symbol: str, bars: pd.DataFrame) -> SymbolScore:
        parts = {}
        total = 0.0
        for rule, weight in self._rules:
            try:
                result = rule.evaluate(bars)
                result = RuleResult(clamp01(result.score), result.detail)
            except Exception as exc:
                result = RuleResult(0.0, f"error: {exc}")
            parts[rule.name] = result
            total += result.score * weight
        return SymbolScore(symbol, total / self._weight_sum, parts)

    def rank(self, bars_by_symbol: dict, top_n: int) -> list:
        scores = [self.score_symbol(sym, bars) for sym, bars in bars_by_symbol.items()]
        scores.sort(key=lambda s: s.total, reverse=True)
        return scores[:top_n]
