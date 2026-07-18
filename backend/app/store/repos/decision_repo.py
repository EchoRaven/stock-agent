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
