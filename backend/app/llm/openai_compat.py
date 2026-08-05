"""OpenAI 兼容 chat/completions 的最小封装(与 GeminiClient 同契约)。

用于把委员会/评测换到更强的模型(llama.com 网关的 gpt-5.5 / deepseek-v4-pro /
kimi-k3,或 deepseek 直连)。契约与 app.llm.gemini.GeminiClient 完全一致:
generate_json(prompt) -> dict | None,**绝不抛异常、绝不崩调用方**;无 key/请求
失败/解析失败一律返回 None 并告警。

安全红线不变:LLM 输出仍在 committee_service 里全量 clamp;本客户端只负责"把
prompt 发出去、把 JSON 取回来",不做任何决策。key 只从传入值/环境读,绝不硬编码、
绝不写日志。
"""
import json
import logging
import re

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 120.0         # 强推理模型(kimi/gpt5.5)比 gemini 慢,给足
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_MAX_TOKENS = 32000      # 推理模型 reasoning 吃 token 预算;是上限非目标(用多少算
                                # 多少,不额外花钱/变慢),给足只防截断。可按端点上限调更高。

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class OpenAICompatClient:
    """generate_json(prompt) -> dict | None。任意 OpenAI 兼容 chat/completions 端点。"""

    def __init__(self, base_url: str, api_key: str, model: str,
                 timeout: float = DEFAULT_TIMEOUT, max_attempts: int = DEFAULT_MAX_ATTEMPTS,
                 max_tokens: int = DEFAULT_MAX_TOKENS, temperature: float | None = None):
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key or ""
        self._model = model
        self._timeout = timeout
        self._max_attempts = max(1, max_attempts)
        self._max_tokens = max_tokens
        # gpt-5.x 等推理模型只接受默认 temperature,传 0 会 400——默认不发送 temperature
        # (我们靠多次重复 reps 来量方差,不依赖单次确定性)。需要时可显式传。
        self._temperature = temperature

    def generate_json(self, prompt: str) -> dict | None:
        if not self._api_key:
            logger.warning("openai_compat: api_key 未配置,跳过 LLM 调用(返回 None)")
            return None
        resp = self._post_with_retry(prompt)
        if resp is None:
            return None
        if resp.status_code != 200:
            logger.warning("openai_compat[%s] 非 200(%s),返回 None", self._model,
                           resp.status_code)
            return None
        return self._parse(resp)

    def _post_with_retry(self, prompt: str):
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": "你只输出一个 JSON 对象,不要任何多余文字、"
                                              "不要 markdown 代码块、不要解释。"},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": self._max_tokens,
        }
        if self._temperature is not None:
            body["temperature"] = self._temperature
        resp = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                resp = httpx.post(self._url, json=body, timeout=self._timeout,
                                  headers={"Authorization": f"Bearer {self._api_key}",
                                           "Content-Type": "application/json"})
            except httpx.HTTPError as exc:
                logger.warning("openai_compat[%s] 传输失败(第 %d/%d 次,%s)",
                               self._model, attempt, self._max_attempts, exc)
                resp = None
                continue
            # 4xx 不重试(参数/鉴权问题);5xx 重试
            if resp.status_code == 200 or resp.status_code < 500:
                return resp
            logger.warning("openai_compat[%s] 返回 %d(第 %d/%d 次)", self._model,
                           resp.status_code, attempt, self._max_attempts)
        return resp

    def _parse(self, resp) -> dict | None:
        try:
            payload = resp.json()
            msg = payload["choices"][0]["message"]
            text = (msg.get("content") or msg.get("reasoning_content") or "").strip()
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            logger.warning("openai_compat[%s] 响应结构异常(%s),返回 None", self._model, exc)
            return None
        parsed = _extract_json_object(text)
        if not isinstance(parsed, dict):
            logger.warning("openai_compat[%s] 未能解析出 JSON 对象,返回 None", self._model)
            return None
        return parsed


def _extract_json_object(text: str):
    """从模型输出里稳健取出第一个 JSON 对象:先直接 loads;失败则剥 markdown 围栏;
    再失败则截取第一个 '{' 到最后一个 '}' 尝试。任何失败返回 None。"""
    if not text:
        return None
    for candidate in _json_candidates(text):
        try:
            obj = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _json_candidates(text: str):
    yield text
    m = _FENCE_RE.search(text)
    if m:
        yield m.group(1).strip()
    lo, hi = text.find("{"), text.rfind("}")
    if 0 <= lo < hi:
        yield text[lo:hi + 1]
