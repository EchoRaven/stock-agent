"""REST API 共享依赖装配:唯一注入点。

测试通过 `app.dependency_overrides[get_session] = ...` /
`app.dependency_overrides[get_provider] = ...` 注入内存 SQLite session 与 fake
行情源,做到全离线(见 tests/conftest.py 的联网熔断)。生产路径复用
app/mcp/runtime.py 的既有装配,不重新实现 engine/session/provider 构造。
"""
from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.config import get_settings
from app.data.base import PriceProvider
from app.data.news_factory import build_news_provider
from app.data.news_finnhub import NewsProvider
from app.llm.gemini import GeminiClient
from app.mcp import runtime


def get_session() -> Iterator[Session]:
    """每请求一个 session;无论成功/异常都在 finally 里关闭。"""
    session = runtime.open_session()
    try:
        yield session
    finally:
        session.close()


def get_provider() -> PriceProvider:
    """服务端行情源(唯一取价通道)。调用方 payload 没有价格通道。"""
    return runtime.get_price_provider()


def get_news_provider() -> NewsProvider:
    """服务端新闻源(唯一取新闻通道)。测试通过 dependency_overrides 注入 fake,离线。"""
    return build_news_provider(get_settings())


def get_gemini_client() -> GeminiClient | None:
    """有 key 才装配 GeminiClient;无 key 返回 None——news_sentiment_service 对
    None client 是安全的(跳过打分,不抛异常)。"""
    settings = get_settings()
    return GeminiClient() if settings.gemini_api_key else None
