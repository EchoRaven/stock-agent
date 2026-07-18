import datetime as dt

from app.backtest.engine import BacktestConfig, BacktestEngine
from app.mcp import runtime
from app.screener.universe import load_universe
from app.services.analysis_service import default_screener
from app.services.market_data_service import fetch_bars


def run_backtest(start: str, end: str, cash: float = 100_000.0, max_positions: int = 5) -> dict:
    """quant-only 历史回测(纯规则,不经 LLM)。start/end 为 ISO 日期串。"""
    try:
        start_d, end_d = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    except ValueError as exc:
        return {"status": "error", "error": f"invalid date: {exc}"}
    try:
        # BacktestConfig.__post_init__ 会对 start>end / cash<=0 / max_positions<1 抛
        # ValueError(M1 hardening 加的校验),必须和 .run() 在同一个 try 里兜住。
        config = BacktestConfig(start=start_d, end=end_d, initial_cash=cash,
                                max_positions=max_positions)
        fetch_start = start_d - dt.timedelta(days=config.lookback_days)
        bars, skipped = fetch_bars(runtime.get_price_provider(), load_universe(None),
                                   fetch_start, end_d)
        if not bars:
            return {"status": "error", "error": "no bars fetched for universe"}
        result = BacktestEngine(bars, default_screener(), config).run()
    except ValueError as exc:
        return {"status": "error", "error": str(exc)}
    return {
        "status": "ok",
        "start": start,
        "end": end,
        "metrics": {k: round(float(v), 6) for k, v in result.metrics.items()},
        "final_equity": round(float(result.equity_curve.iloc[-1]), 2),
        "num_days": int(len(result.equity_curve)),
        "skipped": [{"symbol": sym, "reason": reason} for sym, reason in skipped],
    }
