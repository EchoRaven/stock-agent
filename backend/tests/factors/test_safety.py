"""HARD SAFETY RULE 的端到端证明(Phase 4):LLM 提案是纯不可信数据,从不
被执行。这里直接用攻击式 payload(`factor="__import__"`、
`params={"window": "; rm -rf"}`)喂给 validate_params / propose_factors /
build_factor,断言:(a) 校验干净返回 False,不抛异常;(b) proposer 把它们
静默丢弃,不崩、也不会让恶意载荷混进保留下来的提案里;(c) build_factor 只
raise ValueError,绝不会把这种数据传给任何工厂函数(即绝不"执行")。
"""
import pytest

from app.factors.catalog import FACTORS, build_factor, validate_params
from app.factors.proposer import propose_factors

MALICIOUS_FACTOR_NAME = "__import__"
MALICIOUS_PARAMS = {"window": "; rm -rf"}


def test_malicious_factor_name_rejected_by_validate_params_without_exception():
    ok, err = validate_params(MALICIOUS_FACTOR_NAME, {"window": 60})
    assert ok is False
    assert err  # 有解释性错误,不是静默通过


def test_malicious_params_value_rejected_by_validate_params_without_exception():
    ok, err = validate_params("momentum", MALICIOUS_PARAMS)
    assert ok is False
    assert err


def test_malicious_factor_name_raises_value_error_never_reaches_factory():
    calls = []
    original = {name: spec.build for name, spec in FACTORS.items()}
    for name, spec in FACTORS.items():
        def _spy(p, _orig=spec.build, _name=name):
            calls.append(_name)
            return _orig(p)
        object.__setattr__(spec, "build", _spy)
    try:
        with pytest.raises(ValueError):
            build_factor(MALICIOUS_FACTOR_NAME, {"window": 60})
        with pytest.raises(ValueError):
            build_factor("momentum", MALICIOUS_PARAMS)
    finally:
        for name, spec in FACTORS.items():
            object.__setattr__(spec, "build", original[name])
    assert calls == []  # 没有任何工厂函数被调用过


class _MaliciousGemini:
    """模拟一个被攻破/被诱导的 gemini_client:混入代码样的 factor 名和 params。"""

    def generate_json(self, prompt):
        return {"proposals": [
            {"factor": MALICIOUS_FACTOR_NAME, "params": {"window": 60},
             "hypothesis": "__import__('os').system('rm -rf /')"},
            {"factor": "momentum", "params": MALICIOUS_PARAMS, "hypothesis": "bad params"},
            {"factor": "momentum", "params": {"window": 60}, "hypothesis": "the one legit proposal"},
        ]}


def test_proposer_drops_malicious_items_without_raising_and_keeps_only_valid_ones():
    proposals = propose_factors(_MaliciousGemini(), n=3)
    assert len(proposals) == 1
    assert proposals[0]["factor"] == "momentum"
    assert proposals[0]["params"] == {"window": 60}
    # 每条幸存的提案都必须再次通过目录校验(纵深防御)。
    for p in proposals:
        ok, _err = validate_params(p["factor"], p["params"])
        assert ok is True


def test_proposer_survivors_are_always_buildable_by_build_factor():
    proposals = propose_factors(_MaliciousGemini(), n=3)
    for p in proposals:
        rule = build_factor(p["factor"], p["params"])
        assert rule is not None
