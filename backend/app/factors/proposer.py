"""Phase 4:LLM 因子提案 -> 目录校验过滤。

安全红线:`gemini_client.generate_json` 的返回值被当成纯不可信外部数据处理,
从不 eval/exec。每一条提案在这里都要经 `app.factors.catalog.validate_params`
校验,不合法的(目录之外的名字、非 int/越界参数、乱七八糟的形状)一律丢弃;
gemini_client 为 None、调用异常、响应畸形/None,统统优雅退化为一组确定性的
"种子提案"——proposer 本身永不因为 LLM 的输出抛出未处理异常。
"""
import logging

from app.factors.catalog import catalog_for_prompt, validate_params

logger = logging.getLogger(__name__)

_MAX_HYPOTHESIS_LEN = 200

# 确定性种子提案:gemini_client=None 或响应不可用时的兜底,均在目录范围内。
_SEED_PROPOSALS = [
    {"factor": "momentum", "params": {"window": 60}, "hypothesis": "中期动量延续"},
    {"factor": "low_volatility", "params": {"window": 20}, "hypothesis": "低波动风险调整后收益更优"},
    {"factor": "rsi_strength", "params": {"window": 14}, "hypothesis": "RSI 健康区间捕捉持续强势"},
]


def _seed_proposals(n: int) -> list:
    return [dict(p) for p in _SEED_PROPOSALS[: max(0, n)]]


def _build_prompt(n: int, avoid: list) -> str:
    avoid_line = f"请避免以下已经试过的因子名:{', '.join(avoid)}。\n" if avoid else ""
    return (
        f"{catalog_for_prompt()}\n\n"
        f"请提出 {n} 个候选因子,只能从上面目录中选择 factor 名称、并使用范围内的"
        f"整数参数,不要给出任何代码/表达式。\n{avoid_line}"
        '严格按以下 JSON 格式返回,不要包含任何解释性文字或代码块标记:\n'
        '{"proposals": [{"factor": "<目录内的名字>", "params": {"<param>": <int>, ...}, '
        '"hypothesis": "<一句话说明为什么可能有用>"}]}'
    )


def _clamp_hypothesis(value) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    return value if len(value) <= _MAX_HYPOTHESIS_LEN else value[: _MAX_HYPOTHESIS_LEN - 1] + "…"


def _extract_raw_proposals(raw) -> list:
    if not isinstance(raw, dict):
        return []
    proposals = raw.get("proposals")
    return proposals if isinstance(proposals, list) else []


def propose_factors(gemini_client, *, n: int = 3, avoid=()) -> list:
    """返回 <= n 条已通过目录校验的合法提案。gemini_client 的任何失败模式
    (None/异常/畸形响应)都退化为确定性种子提案,绝不抛出。每条提案都是纯
    数据(factor 名 + 整数 params + 一句话 hypothesis),从不被执行。"""
    avoid = list(avoid or [])
    raw = None
    if gemini_client is not None:
        try:
            raw = gemini_client.generate_json(_build_prompt(n, avoid))
        except Exception:
            logger.warning("factor proposer: gemini 调用失败,回退到种子提案", exc_info=True)
            raw = None

    candidates = _extract_raw_proposals(raw)
    if not candidates:
        candidates = _seed_proposals(n)

    kept: list = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        factor = item.get("factor")
        params = item.get("params")
        ok, _err = validate_params(factor, params)
        if not ok:
            continue
        if factor in avoid:
            continue
        kept.append({
            "factor": factor,
            "params": dict(params),
            "hypothesis": _clamp_hypothesis(item.get("hypothesis")),
        })
        if len(kept) >= n:
            break
    return kept
