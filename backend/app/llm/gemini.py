"""Gemini generateContent 的最小封装。

安全红线:LLM 是外部服务,一律 timeout+重试+优雅降级——无 key/请求失败/
响应解析失败都返回 None 并告警,绝不抛异常、绝不崩溃调用方。
key 只从 Settings(环境变量 / backend/.env)读取,绝不硬编码。
"""
import json
import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_ATTEMPTS = 2


class GeminiClient:
    """generate_json(prompt) -> dict | None。失败一律返回 None,不崩。"""

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 timeout: float = DEFAULT_TIMEOUT, max_attempts: int = DEFAULT_MAX_ATTEMPTS):
        settings = get_settings()
        self._api_key = settings.gemini_api_key if api_key is None else api_key
        self._model = settings.gemini_model if model is None else model
        self._timeout = timeout
        self._max_attempts = max(1, max_attempts)

    def generate_json(self, prompt: str) -> dict | None:
        if not self._api_key:
            logger.warning("gemini_api_key 未配置,跳过 LLM 调用(返回 None)")
            return None

        resp = self._post_with_retry(prompt)
        if resp is None:
            return None
        if resp.status_code != 200:
            logger.warning("gemini 返回非 200 状态码(%s),返回 None", resp.status_code)
            return None
        return self._parse(resp)

    def _post_with_retry(self, prompt: str):
        url = GEMINI_URL.format(model=self._model)
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
        }
        resp = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                resp = httpx.post(url, params={"key": self._api_key}, json=body,
                                   timeout=self._timeout)
            except httpx.HTTPError as exc:
                logger.warning("gemini 请求传输失败(第 %d/%d 次,%s)",
                               attempt, self._max_attempts, exc)
                resp = None
                continue
            if resp.status_code == 200 or resp.status_code < 500:
                return resp
            logger.warning("gemini 返回 %d(第 %d/%d 次)", resp.status_code, attempt,
                           self._max_attempts)
        return resp

    def _parse(self, resp) -> dict | None:
        try:
            payload = resp.json()
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(text)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            logger.warning("gemini 响应解析失败(%s),返回 None", exc)
            return None
        if not isinstance(parsed, dict):
            logger.warning("gemini 响应 JSON 顶层不是对象(%s),返回 None", type(parsed))
            return None
        return parsed
