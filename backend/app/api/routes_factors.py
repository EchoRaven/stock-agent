"""POST /api/factors/mine —— Phase 4:evidence-gated 自主因子挖掘(按需触发)。

安全:LLM 只产出结构化提案(factor 名 + 整数 params over 固定目录),从不产出/
执行任何代码(见 app.factors.catalog/proposer 的校验);每条提案经两窗口回测
门禁,只有稳健改善的记 validated,写入 app/store/repos/memory_repo.py 的
factor 知识库条目——ADVISORY CONTEXT ONLY,不碰任何下单/风控路径(见
tests/test_memory_advisory_isolation.py 的自动化守卫)。provider/gemini_client
全部走 app.api.deps 的专用依赖,测试整体覆盖注入 fake,离线。状态变更(写库)
挂 token 门禁,与 POST /api/trade/cycle 同款。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_gemini_client, get_provider, get_session
from app.api.schemas import MineFactorsRequest
from app.api.security import require_token
from app.data.base import PriceProvider
from app.factors.miner import mine_factors
from app.llm.gemini import GeminiClient

logger = logging.getLogger(__name__)

router = APIRouter(tags=["factors"])


@router.post("/factors/mine", dependencies=[Depends(require_token)])
def mine_factors_route(
    body: MineFactorsRequest = MineFactorsRequest(),
    provider: PriceProvider = Depends(get_provider),
    gemini_client: GeminiClient | None = Depends(get_gemini_client),
    session: Session = Depends(get_session),
) -> dict:
    try:
        results = mine_factors(session, provider, gemini_client, n=body.n)
        session.commit()
    except Exception as exc:
        logger.exception("factor mining failed")
        raise HTTPException(status_code=500,
                            detail=f"factor mining failed: {type(exc).__name__}") from exc
    return {"results": results, "count": len(results)}
