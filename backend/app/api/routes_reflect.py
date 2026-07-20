"""POST /api/reflect —— 平仓复盘(Phase 2):对已平仓的模拟盘交易补写/追平
trade_review 知识库条目(均价法确定性算出的已实现盈亏 + 可选 LLM 教训)。

正常情况下 app/services/trade_cycle_service.py 每轮交易循环后已经自动跑过一次
(见其 settle 之后的钩子);本端点是补跑/手动触发的通道(如离线状态下有新成交
但还没跑过循环、或想立即拿到最新复盘而不等下一轮)。

安全红线:ADVISORY CONTEXT ONLY——只读 app/store/repos/paper_repo.py 的成交
流水与 app/store/repos/decision_repo.py 的历史决定,只写
app/store/repos/memory_repo.py 的 memory_entries 表;不经过、也不可能触发
app/execution/order_manager.py 或 app/risk/ 下任何下单/风控路径(见
tests/test_memory_advisory_isolation.py 的自动化守卫)。幂等(以 sell_fill_id
为键,重复调用返回 0 条新增);状态变更(写库)挂 token 门禁,与 POST
/api/memory 同款(见 app/api/security.py)。
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_gemini_client, get_session
from app.api.security import require_token
from app.llm.gemini import GeminiClient
from app.services.reflection_service import reflect_on_closed_trades

router = APIRouter(tags=["reflect"])


@router.post("/reflect", dependencies=[Depends(require_token)])
def run_reflect_route(
    gemini_client: GeminiClient | None = Depends(get_gemini_client),
    session: Session = Depends(get_session),
) -> dict:
    reviews = reflect_on_closed_trades(session, gemini_client)
    session.commit()
    return {"reviews": reviews, "count": len(reviews)}
