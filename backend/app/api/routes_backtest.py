"""POST /api/backtest —— on-demand 历史回测。全 API 里唯一允许联网的端点(拉行情)。

薄壳:装配 BacktestConfig/BacktestEngine,不重新实现任何回测逻辑。
"""
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_provider
from app.api.schemas import BacktestRequest
from app.api.security import require_token
from app.backtest.engine import BacktestConfig, BacktestEngine
from app.data.base import PriceProvider
from app.screener.universe import load_universe
from app.services.analysis_service import default_screener
from app.services.market_data_service import fetch_bars

router = APIRouter(tags=["backtest"])

MAX_UNIVERSE_SIZE = 50
MAX_DATE_RANGE_DAYS = 2000


@router.post("/backtest", dependencies=[Depends(require_token)])
def run_backtest_route(body: BacktestRequest,
                       provider: PriceProvider = Depends(get_provider)) -> dict:
    if body.universe is not None and len(body.universe) > MAX_UNIVERSE_SIZE:
        raise HTTPException(status_code=400,
                            detail=f"universe too large (max {MAX_UNIVERSE_SIZE} symbols)")
    if (body.end - body.start).days > MAX_DATE_RANGE_DAYS:
        raise HTTPException(status_code=400,
                            detail=f"date range too wide (max {MAX_DATE_RANGE_DAYS} days)")
    try:
        # __post_init__ 校验 start<=end / cash>0 / max_positions>=1,坏输入不触发任何取价
        config = BacktestConfig(start=body.start, end=body.end, initial_cash=body.cash,
                                max_positions=body.max_positions)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    universe = body.universe or load_universe(None)
    fetch_start = body.start - dt.timedelta(days=config.lookback_days)
    bars, skipped = fetch_bars(provider, universe, fetch_start, body.end)
    if not bars:
        raise HTTPException(status_code=400, detail="no bars fetched for universe")

    try:
        result = BacktestEngine(bars, default_screener(), config).run()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "metrics": {k: round(float(v), 6) for k, v in result.metrics.items()},
        "equity_curve": [{"date": ts.date().isoformat(), "equity": round(float(v), 2)}
                         for ts, v in result.equity_curve.items()],
        "skipped": [{"symbol": sym, "reason": reason} for sym, reason in skipped],
    }
