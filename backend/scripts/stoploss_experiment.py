"""Objective backtest comparison of stop-loss / drawdown-control variants.

Research-only harness for the stop-loss experiment: does adding a per-day
stop-loss to the backtest cut the baseline's -18.57% max drawdown without
wrecking return? Mirrors scripts/strategy_experiment.py's structure (same
two fixed windows, same universe/config, fetch-bars-once-per-period, same
table printer) but compares BacktestEngine (baseline, no stop) against
StopLossEngine running each stop mode, using the SAME default_screener
weights (trend.4/mom.4/vol.2) for every variant -- only the stop rule
changes, not the screener.

NOT part of the offline pytest suite (lives under scripts/, outside
pytest's testpaths=["tests"]) and requires network (yfinance, cached via
data_cache/, same as strategy_experiment.py).

Usage:
    .venv/bin/python -m scripts.stoploss_experiment
    .venv/bin/python -m scripts.stoploss_experiment --periods baseline_2024H2_2025H1
"""

import argparse
import datetime as dt
import sys

from app.backtest.engine import BacktestConfig, BacktestEngine
from app.backtest.stoploss_engine import StopLossConfig, StopLossEngine
from app.config import get_settings
from app.data.cache import CachedPriceProvider
from app.data.prices_yfinance import YFinancePriceProvider
from app.screener.universe import DEFAULT_UNIVERSE
from app.services.analysis_service import default_screener
from app.services.market_data_service import fetch_bars

# Same fixed, reproducible windows as scripts/strategy_experiment.py.
PERIODS = {
    "baseline_2024H2_2025H1": (dt.date(2024, 7, 1), dt.date(2025, 6, 30)),
    "robustness_2023H2_2024H1": (dt.date(2023, 7, 1), dt.date(2024, 6, 30)),
}

# variant name -> stop_mode + kwargs override on StopLossConfig
STOP_VARIANTS = {
    "baseline (no stop)": None,
    "fixed_pct (8%)": dict(stop_mode="fixed_pct", stop_fixed_pct=0.08),
    "atr (2.5x ATR14)": dict(stop_mode="atr", stop_atr_mult=2.5, stop_atr_window=14),
    "trailing (10%)": dict(stop_mode="trailing", stop_trailing_pct=0.10),
    "portfolio_dd (15%, pause 5d)": dict(
        stop_mode="portfolio_dd", stop_portfolio_dd_pct=0.15, stop_portfolio_pause_days=5,
    ),
}


def _provider():
    settings = get_settings()
    return CachedPriceProvider(YFinancePriceProvider(), settings.cache_dir), settings


def fetch_period_bars(provider, symbols, start, end, lookback_days) -> dict:
    fetch_start = start - dt.timedelta(days=lookback_days)
    bars, skipped = fetch_bars(provider, symbols, fetch_start, end)
    if skipped:
        print(f"[warn] skipped {len(skipped)}: {skipped}")
    return bars


def run_variant(name: str, stop_kwargs, bars_by_symbol: dict, base_cfg: BacktestConfig) -> dict:
    if stop_kwargs is None:
        result = BacktestEngine(bars_by_symbol, default_screener(), base_cfg).run()
    else:
        cfg = StopLossConfig(
            start=base_cfg.start, end=base_cfg.end, initial_cash=base_cfg.initial_cash,
            max_positions=base_cfg.max_positions, min_score=base_cfg.min_score,
            lookback_days=base_cfg.lookback_days, slippage_bps=base_cfg.slippage_bps,
            **stop_kwargs,
        )
        result = StopLossEngine(bars_by_symbol, default_screener(), cfg).run()
    row = {"variant": name}
    row.update(result.metrics)
    return row


def _fmt(value, col: str) -> str:
    if col in ("total_return", "max_drawdown", "win_rate"):
        return f"{value:.2%}"
    if col == "sharpe":
        return f"{value:.3f}"
    if col == "num_fills":
        return str(int(value))
    return str(value)


def print_table(rows: list) -> None:
    cols = ["variant", "total_return", "max_drawdown", "sharpe", "win_rate", "num_fills"]
    widths = {c: max(len(c), *(len(_fmt(r[c], c)) for r in rows)) for c in cols}
    print(" | ".join(c.ljust(widths[c]) for c in cols))
    print("-+-".join("-" * widths[c] for c in cols))
    for r in rows:
        print(" | ".join(_fmt(r[c], c).ljust(widths[c]) for c in cols))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Stop-loss / drawdown-control comparison harness")
    parser.add_argument("--periods", nargs="*", default=list(PERIODS.keys()), choices=list(PERIODS.keys()))
    parser.add_argument("--max-positions", type=int, default=5)
    parser.add_argument("--cash", type=float, default=100_000.0)
    args = parser.parse_args(argv)

    provider, _settings = _provider()
    symbols = list(DEFAULT_UNIVERSE)

    for period_name in args.periods:
        start, end = PERIODS[period_name]
        config = BacktestConfig(start=start, end=end, initial_cash=args.cash, max_positions=args.max_positions)
        print(f"\n=== {period_name}: {start.isoformat()} -> {end.isoformat()} "
              f"(cash={config.initial_cash:,.0f}, max_positions={config.max_positions}, "
              f"min_score={config.min_score}, lookback_days={config.lookback_days}, "
              f"slippage_bps={config.slippage_bps}, universe={len(symbols)} symbols) ===")

        bars = fetch_period_bars(provider, symbols, start, end, config.lookback_days)

        rows = [run_variant(name, kwargs, bars, config) for name, kwargs in STOP_VARIANTS.items()]
        print_table(rows)

    return 0


if __name__ == "__main__":
    sys.exit(main())
