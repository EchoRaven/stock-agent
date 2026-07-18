import pytest

from app.screener.base import Rule, RuleResult, Screener, SymbolScore, clamp01
from tests.helpers import make_bars


class FixedRule(Rule):
    def __init__(self, name, score):
        self.name = name
        self._score = score

    def evaluate(self, bars):
        return RuleResult(self._score, f"fixed {self._score}")


class BoomRule(Rule):
    name = "boom"

    def evaluate(self, bars):
        raise ValueError("boom")


def test_clamp01():
    assert clamp01(-1) == 0.0
    assert clamp01(0.5) == 0.5
    assert clamp01(2.0) == 1.0


def test_weighted_total():
    s = Screener([(FixedRule("a", 1.0), 3.0), (FixedRule("b", 0.0), 1.0)])
    out = s.score_symbol("X", make_bars())
    assert out.total == pytest.approx(0.75)
    assert out.parts["a"].score == 1.0


def test_rule_exception_scores_zero():
    s = Screener([(BoomRule(), 1.0), (FixedRule("a", 1.0), 1.0)])
    out = s.score_symbol("X", make_bars())
    assert out.parts["boom"].score == 0.0
    assert "boom" in out.parts["boom"].detail
    assert out.total == pytest.approx(0.5)


def test_score_clamped():
    s = Screener([(FixedRule("hot", 5.0), 1.0)])
    assert s.score_symbol("X", make_bars()).total == 1.0


def test_rank_sorts_and_truncates():
    class Half(Rule):
        name = "a"

        def evaluate(self, bars):
            return RuleResult(0.5 if len(bars) < 5 else 1.0, "")

    s = Screener([(Half(), 1.0)])
    ranked = s.rank({"LOW": make_bars(days=3), "HIGH": make_bars(days=10)}, top_n=1)
    assert len(ranked) == 1
    assert ranked[0].symbol == "HIGH"


def test_empty_rules_rejected():
    with pytest.raises(ValueError):
        Screener([])
