"""POST /api/sentiment —— 新闻情绪:装配 news provider + (可选) Gemini,委托
news_sentiment_service.get_symbol_sentiment(注入清洗/打分 clamp/fail-safe 全在那层)。

安全红线:这是会触发外部计费调用(新闻源 + 付费 Gemini)的端点,必须 token
门禁 + POST(不接受不带预检的 CORS "simple request" GET),且 days/max_items
必须有界(SentimentRequest 里的 Field(ge=..., le=...))——否则 days 过大会让
dt.timedelta 溢出触发未处理 500,任意跨站页面还能循环调用它无限放大计费成本。
provider/client 走 app.api.deps 的专用依赖,测试可整体覆盖注入 fake,离线。
"""
import datetime as dt
import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_gemini_client, get_news_provider
from app.api.schemas import SentimentRequest
from app.api.security import require_token
from app.data.news_finnhub import NewsProvider
from app.llm.gemini import GeminiClient
from app.services.news_sentiment_service import get_symbol_sentiment
from app.util.trading_day import et_trading_day

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sentiment"])


@router.post("/sentiment", dependencies=[Depends(require_token)])
def get_sentiment_route(body: SentimentRequest,
                        provider: NewsProvider = Depends(get_news_provider),
                        client: GeminiClient | None = Depends(get_gemini_client)) -> dict:
    symbol = body.symbol.strip()
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol must not be empty")
    as_of = et_trading_day(dt.datetime.now(dt.UTC))
    try:
        return get_symbol_sentiment(provider, client, symbol, as_of,
                                    days=body.days, max_items=body.max_items,
                                    score=bool(client))
    except Exception as exc:
        logger.error("sentiment lookup failed for %s: %s", symbol, exc)
        raise HTTPException(status_code=500,
                            detail=f"sentiment lookup failed: {type(exc).__name__}") from exc
