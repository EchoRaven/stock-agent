"""MCP 工具共享的依赖装配。测试 monkeypatch 本模块四个函数注入 fake。"""
from sqlalchemy.orm import Session

from app.config import get_settings
from app.data.cache import CachedPriceProvider
from app.data.fundamentals_edgar import EdgarFundamentalsProvider
from app.data.news_finnhub import FinnhubNewsProvider
from app.data.prices_yfinance import YFinancePriceProvider
from app.store.db import init_db, make_engine, make_session_factory

_engine = None
_engine_path = None


def get_price_provider():
    settings = get_settings()
    return CachedPriceProvider(YFinancePriceProvider(), settings.cache_dir)


def get_news_provider():
    return FinnhubNewsProvider(get_settings().finnhub_api_key)


def get_fundamentals_provider():
    return EdgarFundamentalsProvider(get_settings().edgar_user_agent)


def open_session() -> Session:
    """按 settings.db_path 惰性建 engine(缓存;路径变更时重建),返回新 session。"""
    global _engine, _engine_path
    path = str(get_settings().db_path)
    if _engine is None or _engine_path != path:
        _engine = make_engine(path)
        init_db(_engine)
        _engine_path = path
    return make_session_factory(_engine)()
