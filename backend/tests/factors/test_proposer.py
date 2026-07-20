"""app.factors.proposer(Phase 4):LLM 提案 -> 目录校验过滤。

安全红线:LLM 的原始输出被当成纯不可信数据对待——每一条提案都要过
app.factors.catalog.validate_params;不合法的一律丢弃,绝不会被执行/eval。
这里证明:(a) 合法提案保留,(b) 目录之外/越界的提案被丢弃,(c) gemini_client
None 或返回畸形数据时优雅退化为确定性种子提案,(d) 恶意 payload(见
tests/factors/test_safety.py 有更聚焦的一版)不会让 propose_factors 抛异常。
"""
from app.factors.catalog import validate_params
from app.factors.proposer import propose_factors


class _FakeGemini:
    def __init__(self, response):
        self._response = response
        self.calls = 0
        self.last_prompt = None

    def generate_json(self, prompt):
        self.calls += 1
        self.last_prompt = prompt
        return self._response


class _RaisingGemini:
    def generate_json(self, prompt):
        raise RuntimeError("network exploded")


def test_gemini_none_falls_back_to_seed_proposals():
    proposals = propose_factors(None, n=3)
    assert 1 <= len(proposals) <= 3
    for p in proposals:
        ok, _err = validate_params(p["factor"], p["params"])
        assert ok is True


def test_gemini_malformed_response_falls_back_to_seeds():
    for bad in (None, [], "not a dict", {"unexpected": "shape"}, {"proposals": "nope"}):
        gemini = _FakeGemini(bad)
        proposals = propose_factors(gemini, n=2)
        assert len(proposals) >= 1
        for p in proposals:
            ok, _err = validate_params(p["factor"], p["params"])
            assert ok is True


def test_gemini_raising_exception_falls_back_to_seeds():
    proposals = propose_factors(_RaisingGemini(), n=2)
    assert len(proposals) >= 1
    for p in proposals:
        ok, _err = validate_params(p["factor"], p["params"])
        assert ok is True


def test_valid_gemini_proposals_are_kept():
    gemini = _FakeGemini({"proposals": [
        {"factor": "momentum", "params": {"window": 45}, "hypothesis": "延续性"},
        {"factor": "rsi_strength", "params": {"window": 14}, "hypothesis": "健康区间"},
    ]})
    proposals = propose_factors(gemini, n=3)
    assert gemini.calls == 1
    assert len(proposals) == 2
    factors = {p["factor"] for p in proposals}
    assert factors == {"momentum", "rsi_strength"}
    for p in proposals:
        ok, _err = validate_params(p["factor"], p["params"])
        assert ok is True


def test_out_of_catalog_name_is_dropped_but_valid_siblings_kept():
    gemini = _FakeGemini({"proposals": [
        {"factor": "momentum", "params": {"window": 45}, "hypothesis": "ok"},
        {"factor": "totally_made_up_factor", "params": {"window": 45}, "hypothesis": "bad"},
    ]})
    proposals = propose_factors(gemini, n=3)
    assert len(proposals) == 1
    assert proposals[0]["factor"] == "momentum"


def test_out_of_bounds_params_are_dropped():
    gemini = _FakeGemini({"proposals": [
        {"factor": "momentum", "params": {"window": 99999}, "hypothesis": "bad"},
        {"factor": "sma_trend", "params": {"fast": 50, "slow": 20}, "hypothesis": "bad order"},
    ]})
    proposals = propose_factors(gemini, n=3)
    assert proposals == []


def test_respects_n_cap():
    gemini = _FakeGemini({"proposals": [
        {"factor": "momentum", "params": {"window": w}, "hypothesis": "h"} for w in (20, 30, 40, 50)
    ]})
    proposals = propose_factors(gemini, n=2)
    assert len(proposals) == 2


def test_avoid_list_is_excluded_even_if_gemini_ignores_instruction():
    gemini = _FakeGemini({"proposals": [
        {"factor": "momentum", "params": {"window": 45}, "hypothesis": "h"},
        {"factor": "rsi_strength", "params": {"window": 14}, "hypothesis": "h"},
    ]})
    proposals = propose_factors(gemini, n=3, avoid=["momentum"])
    factors = {p["factor"] for p in proposals}
    assert "momentum" not in factors
    assert "rsi_strength" in factors


def test_prompt_mentions_catalog_and_avoid_names():
    gemini = _FakeGemini({"proposals": []})
    propose_factors(gemini, n=3, avoid=["momentum"])
    assert "momentum" in gemini.last_prompt
    assert "low_volatility" in gemini.last_prompt  # 目录里的另一个名字也在 prompt 里


def test_hypothesis_is_clamped_to_a_short_string():
    gemini = _FakeGemini({"proposals": [
        {"factor": "momentum", "params": {"window": 45}, "hypothesis": "x" * 5000},
    ]})
    proposals = propose_factors(gemini, n=1)
    assert len(proposals) == 1
    assert len(proposals[0]["hypothesis"]) <= 200


def test_missing_or_non_string_hypothesis_becomes_empty_string():
    gemini = _FakeGemini({"proposals": [
        {"factor": "momentum", "params": {"window": 45}},
        {"factor": "rsi_strength", "params": {"window": 14}, "hypothesis": 12345},
    ]})
    proposals = propose_factors(gemini, n=3)
    assert all(p["hypothesis"] == "" for p in proposals)
