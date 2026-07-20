"""POST /api/picks —— 委员会排序的 AI 荐股列表:在量化筛选(quant screen)基础
上,对每个候选标的额外跑一次 LLM 四角色委员会,按 conviction(action 优先级 +
confidence + quant_score)重新排序返回。

安全红线:纯分析端点,绝不下单——本文件不导入 app.services.decision_service
的 submit_decision 或 app.execution.order_manager 的任何下单路径;编排全权委托
app.services.picks_service.generate_picks,committee 只产出裁决草稿并原样
返回展示,不落库、不生成订单(见 tests/api/test_picks.py 的 no-order/no-decision
断言)。会对每个候选标的触发一次(最多 n 次,默认 8)可能付费的 Gemini 调用,
因此挂 require_token,同 /api/stock/{symbol}/analyze /api/trade/cycle 同款
门禁模式。外部数据源(价格/新闻/财报)+ Gemini 全部走 app.api.deps 的专用依赖
注入,测试整体覆盖注入 fake,离线。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import (get_fundamentals_provider, get_gemini_client, get_news_provider,
                          get_provider, get_session)
from app.api.schemas import PicksRequest
from app.api.security import require_token
from app.config import get_settings
from app.data.base import PriceProvider
from app.data.fundamentals_edgar import FundamentalsProvider
from app.data.news_finnhub import NewsProvider
from app.llm.gemini import GeminiClient
from app.services.picks_service import generate_picks

logger = logging.getLogger(__name__)

router = APIRouter(tags=["picks"])


@router.post("/picks", dependencies=[Depends(require_token)])
def generate_picks_route(
    body: PicksRequest = PicksRequest(),
    provider: PriceProvider = Depends(get_provider),
    news_provider: NewsProvider = Depends(get_news_provider),
    fundamentals_provider: FundamentalsProvider = Depends(get_fundamentals_provider),
    gemini_client: GeminiClient | None = Depends(get_gemini_client),
    session: Session = Depends(get_session),
) -> dict:
    if gemini_client is None and not get_settings().gemini_api_key:
        raise HTTPException(status_code=400, detail="Gemini 未配置,无法生成 AI 荐股")
    try:
        return generate_picks(session, provider, news_provider, fundamentals_provider,
                              gemini_client, n=body.n)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("picks generation failed")
        raise HTTPException(status_code=500,
                            detail=f"picks generation failed: {type(exc).__name__}") from exc
