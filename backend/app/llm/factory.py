"""委员会/评测用 LLM 客户端的工厂——按环境变量选 provider,契约统一是
generate_json(prompt) -> dict | None(GeminiClient / OpenAICompatClient 都满足)。

默认 gemini(线上不变)。评测想换更强模型时,设:
  STOCKAGENT_LLM_PROVIDER=gateway   STOCKAGENT_LLM_MODEL=gpt-5-5-genai-responses
  (网关 key/base 从 GATEWAY_LLAMA_API_KEY / GATEWAY_BASE_URL 读——见
   /data1/common/haibotong/MODEL_ACCESS_GUIDE.md;key 绝不进 git)
  或 STOCKAGENT_LLM_PROVIDER=deepseek(deepseek 直连 DEEPSEEK_API_KEY)。
未配这些环境变量时一律回退 gemini,不会因缺 key 崩。
"""
import logging
import os

from app.llm.gemini import GeminiClient
from app.llm.openai_compat import OpenAICompatClient

logger = logging.getLogger(__name__)

_GATEWAY_DEFAULT_BASE = "https://api.llama.com/experimental/compat/openai/v1"
_DEEPSEEK_BASE = "https://api.deepseek.com"


def make_committee_client():
    """按 STOCKAGENT_LLM_PROVIDER 返回一个 generate_json 客户端。"""
    provider = os.environ.get("STOCKAGENT_LLM_PROVIDER", "gemini").strip().lower()

    if provider == "gateway":
        key = os.environ.get("GATEWAY_LLAMA_API_KEY", "")
        base = os.environ.get("GATEWAY_BASE_URL", _GATEWAY_DEFAULT_BASE)
        model = os.environ.get("STOCKAGENT_LLM_MODEL", "gpt-5-5-genai-responses")
        if not key:
            logger.warning("provider=gateway 但 GATEWAY_LLAMA_API_KEY 未设,回退 gemini")
            return GeminiClient()
        logger.info("committee LLM = gateway:%s", model)
        return OpenAICompatClient(base, key, model)

    if provider == "deepseek":
        key = os.environ.get("DEEPSEEK_API_KEY", "")
        model = os.environ.get("STOCKAGENT_LLM_MODEL", "deepseek-chat")
        if not key:
            logger.warning("provider=deepseek 但 DEEPSEEK_API_KEY 未设,回退 gemini")
            return GeminiClient()
        logger.info("committee LLM = deepseek:%s", model)
        return OpenAICompatClient(_DEEPSEEK_BASE, key, model)

    return GeminiClient()
