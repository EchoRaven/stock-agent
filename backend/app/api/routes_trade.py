"""POST /api/trade/cycle —— screen → 四角色委员会 → 闸门下单 的每日交易循环(按需触发)。

安全:这是会触发外部计费调用(行情 + 新闻 + 付费 Gemini,每评估一个标的一次
LLM 调用)并可能在 semi_auto/full_auto 下建单的端点,必须 token 门禁。委员会
只出建议——真正是否成交仍由 trade_cycle_service → decision_service.submit_decision
→ order_manager 的唯一下单 choke point 决定(读 DB mode + 过 RiskGate);这里
不做任何额外判断。provider/client 全部走 app.api.deps 的专用依赖,测试整体
覆盖注入 fake,离线。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import (get_fundamentals_provider, get_gemini_client, get_news_provider,
                          get_provider, get_session)
from app.api.schemas import TradeCycleRequest
from app.api.security import require_token
from app.data.base import PriceProvider
from app.data.fundamentals_edgar import FundamentalsProvider
from app.data.news_finnhub import NewsProvider
from app.llm.gemini import GeminiClient
from app.services.trade_cycle_service import run_trade_cycle

logger = logging.getLogger(__name__)

router = APIRouter(tags=["trade"])


@router.post("/trade/cycle", dependencies=[Depends(require_token)])
def run_trade_cycle_route(
    body: TradeCycleRequest = TradeCycleRequest(),
    provider: PriceProvider = Depends(get_provider),
    news_provider: NewsProvider = Depends(get_news_provider),
    fundamentals_provider: FundamentalsProvider = Depends(get_fundamentals_provider),
    gemini_client: GeminiClient | None = Depends(get_gemini_client),
    session: Session = Depends(get_session),
) -> dict:
    try:
        return run_trade_cycle(
            session, provider, news_provider, fundamentals_provider, gemini_client,
            settle=body.settle, universe=body.universe, max_eval=body.max_eval,
        )
    except Exception as exc:
        logger.exception("trade cycle failed")
        raise HTTPException(status_code=500,
                            detail=f"trade cycle failed: {type(exc).__name__}") from exc
