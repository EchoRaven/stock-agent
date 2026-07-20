"""GET/POST /api/memory —— agent 知识库(Phase 1)。

ADVISORY CONTEXT ONLY:这里的读写路径完全独立于 app/execution/order_manager.py
与 app/risk/(见 tests/test_memory_advisory_isolation.py 的自动化守卫),不经过
RiskGate,也不可能触发下单——只是 committee_service 提示词检索用的说明性文本。

安全红线:GET 只读,不设 token 门禁(与其它只读端点一致,如 /api/settings、
/api/orders);POST(人工新增知识条目)与 POST /memory/seed(播种实验结论,
幂等)都是状态变更,挂 Depends(require_token) 门禁。
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.api.schemas import MemoryCreate
from app.api.security import require_token
from app.services.memory_service import ensure_seeded
from app.store.models import MemoryEntryRow
from app.store.repos.memory_repo import add_entry, get_entries

router = APIRouter(tags=["memory"])


def _entry_to_dict(row: MemoryEntryRow) -> dict:
    return {
        "id": row.id,
        "kind": row.kind,
        "title": row.title,
        "body": row.body,
        "symbol": row.symbol,
        "status": row.status,
        "evidence_json": row.evidence_json,
        "source": row.source,
        "weight": row.weight,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


@router.get("/memory")
def list_memory_route(kind: str | None = None, symbol: str | None = None,
                      status: str | None = None, limit: int | None = None,
                      session: Session = Depends(get_session)) -> list:
    rows = get_entries(session, kind=kind, symbol=symbol, status=status, limit=limit)
    return [_entry_to_dict(r) for r in rows]


@router.post("/memory", dependencies=[Depends(require_token)])
def create_memory_route(body: MemoryCreate, session: Session = Depends(get_session)) -> dict:
    """人工新增知识条目。source 服务端强制为 "manual"(不采信请求体覆盖)。"""
    try:
        row = add_entry(session, body.kind, body.title, body.body, symbol=body.symbol,
                        status=body.status, weight=body.weight, source="manual")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return _entry_to_dict(row)


@router.post("/memory/seed", dependencies=[Depends(require_token)])
def seed_memory_route(session: Session = Depends(get_session)) -> dict:
    """播种 4 轮实验的真实结论(幂等——已播种过返回 inserted=0)。"""
    count = ensure_seeded(session)
    session.commit()
    return {"inserted": count}
