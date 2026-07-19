from sqlalchemy import select
from sqlalchemy.orm import Session

from app.store.models import AlertRow


def add_alert(session: Session, kind: str, message: str) -> AlertRow:
    row = AlertRow(kind=kind, message=message)
    session.add(row)
    session.flush()
    return row


def get_alerts(session: Session, kind=None) -> list:
    stmt = select(AlertRow).order_by(AlertRow.id)
    if kind is not None:
        stmt = stmt.where(AlertRow.kind == kind)
    return list(session.scalars(stmt))
