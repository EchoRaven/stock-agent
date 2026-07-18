import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.store.models import ReportRow


def save_report(session: Session, report_date: dt.date, content_md: str,
                kind: str = "daily") -> ReportRow:
    """同 (report_date, kind) 覆盖(upsert)。"""
    stmt = select(ReportRow).where(ReportRow.report_date == report_date, ReportRow.kind == kind)
    row = session.scalars(stmt).first()
    if row is None:
        row = ReportRow(report_date=report_date, kind=kind, content_md=content_md)
        session.add(row)
    else:
        row.content_md = content_md
    session.flush()
    return row


def get_report(session: Session, report_date: dt.date, kind: str = "daily"):
    stmt = select(ReportRow).where(ReportRow.report_date == report_date, ReportRow.kind == kind)
    return session.scalars(stmt).first()
