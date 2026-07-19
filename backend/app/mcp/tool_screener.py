import datetime as dt

from app.config import get_settings
from app.mcp import runtime
from app.screener.universe import load_universe
from app.services.analysis_service import run_screen_on_bars
from app.services.market_data_service import fetch_bars
from app.store.repos.heartbeat_repo import record_heartbeat
from app.store.repos.signal_repo import save_signals
from app.util.trading_day import et_trading_day

JOB_PREMARKET = "premarket_screen"


def _heartbeat(ok: bool, detail: str = "") -> None:
    with runtime.open_session() as session:
        record_heartbeat(session, JOB_PREMARKET, ok=ok,
                         ran_at=dt.datetime.now(dt.UTC).replace(tzinfo=None),
                         detail=detail)
        session.commit()


def run_screener(top_n: int = 10) -> dict:
    """盘前筛选:对默认股票池打分排序,取 top_n,并把快照落库 signals 表。

    返回 results(降序:rank/symbol/total/parts)与 skipped(抓取失败的标的)。
    每次运行记录 watchdog 心跳(成功/失败),供自动降级检查。
    """
    if top_n < 1:
        return {"status": "error", "error": "top_n must be >= 1"}
    try:
        settings = get_settings()
        as_of = et_trading_day(dt.datetime.now(dt.UTC))
        start = as_of - dt.timedelta(days=settings.lookback_days)
        bars, skipped = fetch_bars(runtime.get_price_provider(), load_universe(None),
                                   start, as_of)
        scores = run_screen_on_bars(bars, top_n)
        with runtime.open_session() as session:
            save_signals(session, as_of, scores)
            session.commit()
    except Exception as exc:
        _heartbeat(False, str(exc)[:200])
        raise
    _heartbeat(True)
    return {
        "as_of": as_of.isoformat(),
        "results": [
            {
                "rank": rank,
                "symbol": s.symbol,
                "total": round(s.total, 4),
                "parts": {name: {"score": round(r.score, 4), "detail": r.detail}
                          for name, r in s.parts.items()},
            }
            for rank, s in enumerate(scores, 1)
        ],
        "skipped": [{"symbol": sym, "reason": reason} for sym, reason in skipped],
    }
