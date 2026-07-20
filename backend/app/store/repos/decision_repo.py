import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.store.models import DecisionRow


def save_decision(session: Session, as_of: dt.date, symbol: str, action: str,
                  confidence: float, mode: str, payload_json: str) -> DecisionRow:
    row = DecisionRow(as_of=as_of, symbol=symbol, action=action,
                      confidence=confidence, mode=mode, payload_json=payload_json)
    session.add(row)
    session.flush()  # 拿到自增 id
    return row


def get_decisions(session: Session, as_of: dt.date) -> list:
    stmt = select(DecisionRow).where(DecisionRow.as_of == as_of).order_by(DecisionRow.id)
    return list(session.scalars(stmt))


def get_recent_decisions(session: Session, symbol: str | None = None,
                         limit: int = 50) -> list[DecisionRow]:
    """决策历史浏览用:按 symbol 可选过滤,created_at desc(同刻按 id desc 兜底)排序,limit 截断。"""
    stmt = select(DecisionRow)
    if symbol is not None:
        stmt = stmt.where(DecisionRow.symbol == symbol)
    stmt = stmt.order_by(DecisionRow.created_at.desc(), DecisionRow.id.desc()).limit(limit)
    return list(session.scalars(stmt))
