import datetime as dt

from app.config import get_settings
from app.mcp import runtime
from app.screener.universe import load_universe
from app.services.analysis_service import run_screen_on_bars
from app.services.market_data_service import fetch_bars
from app.store.repos.signal_repo import save_signals


def run_screener(top_n: int = 10) -> dict:
    """盘前筛选:对默认股票池打分排序,取 top_n,并把快照落库 signals 表。

    返回 results(降序:rank/symbol/total/parts)与 skipped(抓取失败的标的)。
    """
    settings = get_settings()
    as_of = dt.date.today()
    start = as_of - dt.timedelta(days=settings.lookback_days)
    bars, skipped = fetch_bars(runtime.get_price_provider(), load_universe(None), start, as_of)
    scores = run_screen_on_bars(bars, top_n)
    with runtime.open_session() as session:
        save_signals(session, as_of, scores)
        session.commit()
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
