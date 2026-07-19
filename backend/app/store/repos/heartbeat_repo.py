import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.store.models import HeartbeatRow


def record_heartbeat(session: Session, job: str, ok: bool, ran_at: dt.datetime,
                     detail: str = "") -> HeartbeatRow:
    """记录一次 cron 心跳。ran_at 为 naive-UTC,由调用方注入(便于测试)。"""
    row = HeartbeatRow(job=job, ok=ok, ran_at=ran_at, detail=detail)
    session.add(row)
    session.flush()
    return row


def recent_heartbeats(session: Session, job: str, limit: int = 10) -> list:
    """该 job 最近的心跳,新→旧。"""
    stmt = (select(HeartbeatRow).where(HeartbeatRow.job == job)
            .order_by(HeartbeatRow.ran_at.desc(), HeartbeatRow.id.desc()).limit(limit))
    return list(session.scalars(stmt))
