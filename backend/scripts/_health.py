"""Gemini 健康探活 + fail-safe 熔断 —— 给回放脚本用,防止在配额耗尽(429)时
白烧一整轮、产出一堆 fail-safe(conf=0.0)污染数据。

背景(2026-08-04 踩坑):重度回放耗尽 Gemini 日配额后,GeminiClient 对 429 一律
`generate_json→None`,委员会走 fail-safe(hold, confidence=0.0)。一次 72 条决策
里 76% 是 fail-safe,聚合结果完全无效;而且 replay_eval 会"跳过已存在日期",
坏行不删重跑也脏。这个模块让回放**跑批前先探活、跑批中检测 fail-safe 比例过高
就熔断**,从源头挡住污染数据。
"""


class GeminiUnavailable(RuntimeError):
    """Gemini 探活失败或 fail-safe 比例过高,应当终止本轮回放。"""


def probe_gemini(gemini_client) -> bool:
    """单发一次极小请求探活。返回 True=健康(拿到 dict);False=被挡(429/None)。
    gemini_client 为 None(未配 key)→ False。绝不抛异常。"""
    if gemini_client is None:
        return False
    try:
        r = gemini_client.generate_json('只返回 JSON:{"ok": true}')
        return isinstance(r, dict)
    except Exception:  # noqa: BLE001 - 探活失败一律当不健康
        return False


def require_gemini(gemini_client) -> None:
    """跑批前调用:探活失败就抛 GeminiUnavailable,别开跑。"""
    if not probe_gemini(gemini_client):
        raise GeminiUnavailable(
            "Gemini 探活失败(很可能 429/配额耗尽)。终止,避免产出一堆 fail-safe "
            "污染数据。等配额恢复(日配额约到太平洋午夜重置)再跑;别原地重试。")


class FailSafeGuard:
    """跑批中累计 fail-safe(confidence<=0.05)比例;样本足够且比例过高就熔断。

    用法:每记一条决策后调 observe(confidence);它会在 total>=min_samples 且
    fail-safe 率>threshold 时抛 GeminiUnavailable,让回放早停而不是烧完整轮。
    """

    def __init__(self, threshold: float = 0.5, min_samples: int = 8):
        self.threshold = threshold
        self.min_samples = min_samples
        self.total = 0
        self.failsafe = 0

    def observe(self, confidence: float) -> None:
        self.total += 1
        if confidence is not None and confidence <= 0.05:
            self.failsafe += 1
        if self.total >= self.min_samples and self.failsafe / self.total > self.threshold:
            raise GeminiUnavailable(
                f"fail-safe 比例过高({self.failsafe}/{self.total})——Gemini 很可能中途"
                f"被限流(429)。终止本轮以免产出污染数据;删掉本次独立库后等配额恢复重跑。")
