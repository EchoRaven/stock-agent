"""agent 知识库仓储(Phase 1)。

安全红线:这里的内容只是委员会提示词里的一段说明性文本(ADVISORY CONTEXT
ONLY)——本模块绝不能被 app/execution/order_manager.py 或 app/risk/ 下任何
下单/风控路径导入,查询结果不可能改变 RiskGate 的判定
(见 tests/test_memory_advisory_isolation.py 的自动化守卫)。
"""
import json

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.store.models import MemoryEntryRow

KINDS = ("insight", "factor", "trade_review", "market_note")


def add_entry(session: Session, kind: str, title: str, body: str, *, symbol: str | None = None,
             status: str = "active", evidence: dict | None = None, source: str = "manual",
             weight: float = 1.0) -> MemoryEntryRow:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    row = MemoryEntryRow(
        kind=kind, title=title, body=body, symbol=symbol, status=status,
        evidence_json=json.dumps(evidence or {}, ensure_ascii=False),
        source=source, weight=weight,
    )
    session.add(row)
    session.flush()  # 拿到自增 id
    return row


def get_entries(session: Session, *, kind: str | None = None, symbol: str | None = None,
                status: str | None = None, limit: int | None = None) -> list[MemoryEntryRow]:
    """按提供的字段过滤(未提供的字段不过滤);按 weight desc、updated_at desc 排序。"""
    stmt = select(MemoryEntryRow)
    if kind is not None:
        stmt = stmt.where(MemoryEntryRow.kind == kind)
    if symbol is not None:
        stmt = stmt.where(MemoryEntryRow.symbol == symbol)
    if status is not None:
        stmt = stmt.where(MemoryEntryRow.status == status)
    stmt = stmt.order_by(MemoryEntryRow.weight.desc(), MemoryEntryRow.updated_at.desc())
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt))


def search_entries(session: Session, query: str, *, limit: int = 20) -> list[MemoryEntryRow]:
    """标题/正文大小写不敏感 LIKE 检索。"""
    like = f"%{query}%"
    stmt = (
        select(MemoryEntryRow)
        .where(or_(func.lower(MemoryEntryRow.title).like(func.lower(like)),
                   func.lower(MemoryEntryRow.body).like(func.lower(like))))
        .order_by(MemoryEntryRow.weight.desc(), MemoryEntryRow.updated_at.desc())
        .limit(limit)
    )
    return list(session.scalars(stmt))


def count_entries(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(MemoryEntryRow)) or 0
