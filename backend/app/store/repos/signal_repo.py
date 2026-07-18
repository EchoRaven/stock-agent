import datetime as dt
import json

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.store.models import SignalRow


def save_signals(session: Session, as_of: dt.date, scores: list) -> int:
    """覆盖式写入当日筛选快照(先删同日旧快照)。scores 为 SymbolScore 列表。"""
    session.execute(delete(SignalRow).where(SignalRow.as_of == as_of))
    for rank, score in enumerate(scores, 1):
        parts = {name: {"score": r.score, "detail": r.detail} for name, r in score.parts.items()}
        session.add(SignalRow(as_of=as_of, symbol=score.symbol, rank=rank,
                              total=score.total, parts_json=json.dumps(parts, ensure_ascii=False)))
    return len(scores)


def get_signals(session: Session, as_of: dt.date) -> list:
    stmt = select(SignalRow).where(SignalRow.as_of == as_of).order_by(SignalRow.rank)
    return list(session.scalars(stmt))
