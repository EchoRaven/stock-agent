"""app.factors.catalog(Phase 4):安全因子目录——固定的、参数受限的规则工厂集合。

安全红线:这是整个自主因子挖掘唯一允许"落地"为可执行代码的地方,且代码本身
是我们手写、受审查的;LLM 只能提交 {factor 名, 整数 params} 这样的纯数据,
从不提交/执行任何代码或表达式。validate_params/build_factor 必须把任何
目录之外的名字、任何非整数/越界的参数当纯垃圾数据拒绝掉——这里覆盖该属性
(更贴近攻击场景的端到端覆盖见 tests/factors/test_safety.py)。
"""
import math

import pytest

from app.factors.catalog import FACTORS, build_factor, catalog_for_prompt, validate_params
from tests.helpers import make_bars


# ---------------------------------------------------------------------------
# 每个工厂都能造出一个 Rule,对合成 bars 打分落在 [0,1]。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,params", [
    ("momentum", {"window": 20}),
    ("low_volatility", {"window": 20}),
    ("rsi_strength", {"window": 14}),
    ("sma_trend", {"fast": 10, "slow": 30}),
    ("volume_trend", {"window": 20}),
])
def test_each_factory_builds_a_rule_that_scores_in_unit_interval(name, params):
    rule = build_factor(name, params)
    bars = make_bars(days=120, base=100.0, step=0.3)
    result = rule.evaluate(bars)
    assert 0.0 <= result.score <= 1.0
    assert not math.isnan(result.score)
    assert name in rule.name


def test_insufficient_bars_scores_zero_not_exception():
    rule = build_factor("momentum", {"window": 60})
    bars = make_bars(days=5, base=100.0)
    result = rule.evaluate(bars)
    assert result.score == 0.0


def test_flat_prices_do_not_crash_low_volatility_or_rsi():
    # 全平走势 pct_change 全 0、rsi 全涨全跌以外的边界(NaN 分母)都不能崩。
    bars = make_bars(days=120, base=100.0, step=0.0)
    for name, params in (("low_volatility", {"window": 20}), ("rsi_strength", {"window": 14})):
        rule = build_factor(name, params)
        result = rule.evaluate(bars)
        assert 0.0 <= result.score <= 1.0


def test_momentum_formula_matches_brief_mapping():
    # ret = pct_return(close, window).iloc[-1]; score = clamp01((ret+0.10)/0.40)
    bars = make_bars(days=80, base=100.0, step=1.0)  # 80 天,close 从 100 涨到 179
    rule = build_factor("momentum", {"window": 60})
    ret = bars["close"].iloc[-1] / bars["close"].iloc[-61] - 1.0
    expected = min(max((ret + 0.10) / 0.40, 0.0), 1.0)
    result = rule.evaluate(bars)
    assert result.score == pytest.approx(expected)


def test_catalog_for_prompt_lists_all_factor_names_and_bounds():
    text = catalog_for_prompt()
    for name in FACTORS:
        assert name in text
    assert "10" in text and "120" in text  # momentum window bounds


# ---------------------------------------------------------------------------
# validate_params: 拒绝目录之外的名字 / 越界 / 非整数 / sma_trend fast>=slow。
# ---------------------------------------------------------------------------


def test_validate_params_accepts_in_bounds_proposal():
    ok, err = validate_params("momentum", {"window": 60})
    assert ok is True
    assert err == ""


def test_validate_params_rejects_out_of_catalog_name():
    ok, err = validate_params("not_a_real_factor", {"window": 60})
    assert ok is False
    assert err


def test_validate_params_rejects_out_of_bounds_window():
    ok, _err = validate_params("momentum", {"window": 500})
    assert ok is False
    ok, _err = validate_params("momentum", {"window": 0})
    assert ok is False


def test_validate_params_rejects_non_integer_param():
    ok, _err = validate_params("momentum", {"window": 60.5})
    assert ok is False
    ok, _err = validate_params("momentum", {"window": "60"})
    assert ok is False
    ok, _err = validate_params("momentum", {"window": True})  # bool is not a real int here
    assert ok is False


def test_validate_params_rejects_missing_or_extra_params():
    ok, _err = validate_params("momentum", {})
    assert ok is False
    ok, _err = validate_params("momentum", {"window": 60, "extra": 1})
    assert ok is False


def test_validate_params_rejects_non_dict_params():
    ok, _err = validate_params("momentum", "; rm -rf")
    assert ok is False
    ok, _err = validate_params("momentum", None)
    assert ok is False


def test_validate_params_sma_trend_requires_fast_less_than_slow():
    ok, _err = validate_params("sma_trend", {"fast": 50, "slow": 20})
    assert ok is False
    ok, _err = validate_params("sma_trend", {"fast": 20, "slow": 20})
    assert ok is False
    ok, err = validate_params("sma_trend", {"fast": 10, "slow": 30})
    assert ok is True
    assert err == ""


# ---------------------------------------------------------------------------
# build_factor: 对非法输入一律 raise ValueError,绝不调用工厂函数(即绝不
# "执行"任何越权数据)。
# ---------------------------------------------------------------------------


def test_build_factor_raises_on_out_of_catalog_name():
    with pytest.raises(ValueError):
        build_factor("__import__", {"window": 60})


def test_build_factor_raises_on_bad_params():
    with pytest.raises(ValueError):
        build_factor("momentum", {"window": "; rm -rf"})
    with pytest.raises(ValueError):
        build_factor("sma_trend", {"fast": 50, "slow": 20})
