"""GET /api/signals —— 只读:返回已存快照(cron/CLI 写入),不在此发起实时联网筛选。

POST /api/signals/run —— 按需触发一次筛选(镜像 app/mcp/tool_screener.run_screener),
是全 API 里少数几个允许联网的端点之一(取行情)。token 门禁——这是会写库的动作。
"""
import datetime as dt
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_provider, get_session
from app.api.schemas import SignalsRunRequest
from app.api.security import require_token
from app.config import get_settings
from app.data.base import PriceProvider
from app.screener.universe import DEFAULT_UNIVERSE
from app.services.analysis_service import run_screen_on_bars
from app.services.market_data_service import fetch_bars
from app.store.models import SignalRow
from app.store.repos.signal_repo import get_signals, save_signals
from app.util.trading_day import et_trading_day

logger = logging.getLogger(__name__)

router = APIRouter(tags=["signals"])


def _signal_to_dict(row: SignalRow) -> dict:
    return {"symbol": row.symbol, "rank": row.rank, "total": row.total,
            "parts": json.loads(row.parts_json)}


@router.get("/signals")
def list_signals_route(date: dt.date | None = None,
                       session: Session = Depends(get_session)) -> list:
    as_of = date or et_trading_day(dt.datetime.now(dt.UTC))
    return [_signal_to_dict(r) for r in get_signals(session, as_of)]


@router.post("/signals/run", dependencies=[Depends(require_token)])
def run_signals_route(body: SignalsRunRequest = SignalsRunRequest(),
                      provider: PriceProvider = Depends(get_provider),
                      session: Session = Depends(get_session)) -> list:
    settings = get_settings()
    as_of = et_trading_day(dt.datetime.now(dt.UTC))
    universe = body.universe or DEFAULT_UNIVERSE
    top_n = body.top_n or settings.top_n
    start = as_of - dt.timedelta(days=settings.lookback_days)
    try:
        bars, skipped = fetch_bars(provider, universe, start, as_of)
        if skipped:
            logger.warning("run-screen skipped %d symbol(s): %s", len(skipped), skipped)
        scores = run_screen_on_bars(bars, top_n)
        save_signals(session, as_of, scores)
        session.commit()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500,
                            detail=f"run-screen failed: {exc}") from exc
    return [_signal_to_dict(r) for r in get_signals(session, as_of)]
