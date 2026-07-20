"""GET /api/sentiment —— 新闻情绪:装配 news provider + (可选) Gemini,委托
news_sentiment_service.get_symbol_sentiment(注入清洗/打分 clamp/fail-safe 全在那层)。

只读 GET,不挂 token 门禁(不改状态);允许联网(新闻/LLM 都是外部服务,与
routes_backtest.py 同一豁免)。provider/client 走 app.api.deps 的专用依赖,测试可
整体覆盖注入 fake,离线。
"""
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_gemini_client, get_news_provider
from app.data.news_finnhub import NewsProvider
from app.llm.gemini import GeminiClient
from app.services.news_sentiment_service import get_symbol_sentiment
from app.util.trading_day import et_trading_day

router = APIRouter(tags=["sentiment"])


@router.get("/sentiment")
def get_sentiment_route(symbol: str, days: int = 7, max_items: int = 10,
                        provider: NewsProvider = Depends(get_news_provider),
                        client: GeminiClient | None = Depends(get_gemini_client)) -> dict:
    symbol = (symbol or "").strip()
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol must not be empty")
    as_of = et_trading_day(dt.datetime.now(dt.UTC))
    return get_symbol_sentiment(provider, client, symbol, as_of,
                                days=days, max_items=max_items, score=bool(client))
