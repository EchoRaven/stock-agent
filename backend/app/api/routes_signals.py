"""GET /api/signals —— 只读:返回已存快照(cron/CLI 写入),不在此发起实时联网筛选。"""
import datetime as dt
import json

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.store.models import SignalRow
from app.store.repos.signal_repo import get_signals
from app.util.trading_day import et_trading_day

router = APIRouter(tags=["signals"])


def _signal_to_dict(row: SignalRow) -> dict:
    return {"symbol": row.symbol, "rank": row.rank, "total": row.total,
            "parts": json.loads(row.parts_json)}


@router.get("/signals")
def list_signals_route(date: dt.date | None = None,
                       session: Session = Depends(get_session)) -> list:
    as_of = date or et_trading_day(dt.datetime.now(dt.UTC))
    return [_signal_to_dict(r) for r in get_signals(session, as_of)]
