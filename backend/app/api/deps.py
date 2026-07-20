"""REST API 共享依赖装配:唯一注入点。

测试通过 `app.dependency_overrides[get_session] = ...` /
`app.dependency_overrides[get_provider] = ...` 注入内存 SQLite session 与 fake
行情源,做到全离线(见 tests/conftest.py 的联网熔断)。生产路径复用
app/mcp/runtime.py 的既有装配,不重新实现 engine/session/provider 构造。
"""
from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.data.base import PriceProvider
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
