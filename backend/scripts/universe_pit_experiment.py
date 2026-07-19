"""Point-in-time (selection-bias-reduced) universe backtest comparison.

Follow-up to scripts/universe_experiment.py. That experiment's
`diversified_54` universe was hand-picked by a human from known winners
(selection bias + survivorship bias, since only names that are still around
today could even be picked). This experiment removes the hand-selection
step by comparing against the **point-in-time S&P 500 constituent set as of
each window's start** (backend/data/sp500_pit_2019plus.csv), which includes
names later delisted/renamed/removed from the index.

This does NOT eliminate survivorship bias: the free price source (yfinance)
cannot fetch delisted/renamed tickers (e.g. SIVB, FRC, ABMD, XLNX, ATVI,
ANTM all return "possibly delisted; no data"). That residual gap is
measured directly and reported prominently as requested vs fetched vs
coverage% for every universe, especially `sp500_pit` where it is the whole
point of the experiment. Missing names are NOT worked around or
substituted -- the gap itself is the reported result.

Three universes compared per period, same fixed windows, same screener,
same BacktestConfig as scripts/universe_experiment.py -- only universe
membership varies:
  - default_30    = app.screener.universe.DEFAULT_UNIVERSE (hand-picked, 30 mega-caps)
  - diversified_54 = scripts.universe_experiment.DIVERSIFIED_UNIVERSE (hand-picked, sector-spread)
  - sp500_pit     = point-in-time full S&P 500 constituents as of the window start
                     (~505 names, NOT hand-picked -- includes since-delisted names)

NOT part of the offline pytest suite (lives under scripts/, outside
pytest's testpaths=["tests"]) and requires network (yfinance, cached via
data_cache/, same as universe_experiment.py).

Usage:
    uv run python -m scripts.universe_pit_experiment
    uv run python -m scripts.universe_pit_experiment --periods bear_2022
"""

import argparse
import datetime as dt
import sys

from app.backtest.engine import BacktestConfig, BacktestEngine
from app.config import get_settings
from app.data.cache import CachedPriceProvider
from app.data.prices_yfinance import YFinancePriceProvider
from app.screener.sp500_pit import constituents_asof
from app.screener.universe import DEFAULT_UNIVERSE
from app.services.analysis_service import default_screener
from app.services.market_data_service import fetch_bars

from scripts.universe_experiment import DIVERSIFIED_UNIVERSE, PERIODS, avg_pairwise_correlation


def _provider():
    settings = get_settings()
    return CachedPriceProvider(YFinancePriceProvider(), settings.cache_dir), settings


def fetch_period_bars(provider, symbols, start, end, lookback_days) -> tuple:
    fetch_start = start - dt.timedelta(days=lookback_days)
    return fetch_bars(provider, symbols, fetch_start, end)


def build_universes(period_start: dt.date) -> dict:
    """sp500_pit is point-in-time as of the window start; the other two are
    fixed hand-picked lists reused unchanged from universe_experiment.py."""
    return {
        "default_30": list(DEFAULT_UNIVERSE),
        "diversified_54": list(DIVERSIFIED_UNIVERSE),
        "sp500_pit": constituents_asof(period_start),
    }


def run_universe(name: str, symbols: list, provider, base_cfg: BacktestConfig) -> dict:
    requested = len(symbols)
    bars, skipped = fetch_period_bars(provider, symbols, base_cfg.start, base_cfg.end, base_cfg.lookback_days)
    fetched = len(bars)
    coverage = (fetched / requested) if requested else float("nan")
    missing = requested - fetched

    if name == "sp500_pit":
        print(f"[{name}] 请求 {requested}, 抓到 {fetched} ({coverage:.1%}); "
              f"缺失 {missing} = 退市/更名(残余幸存者偏差); skipped={len(skipped)}")
    else:
        print(f"[{name}] 请求 {requested}, 抓到 {fetched} ({coverage:.1%}); skipped={len(skipped)}")

    avg_corr, n_effective = avg_pairwise_correlation(bars, base_cfg.start, base_cfg.end)
    print(f"[{name}] 平均两两相关: {avg_corr:.3f} (基于 {n_effective} 只有效标的)")

    result = BacktestEngine(bars, default_screener(), base_cfg).run()
    row = {
        "universe": name,
        "requested": requested,
        "fetched": fetched,
        "coverage": coverage,
        "fetched_symbols": set(bars.keys()),
        "avg_pairwise_corr": avg_corr,
    }
    row.update(result.metrics)
    return row


def _fmt(row: dict, col: str) -> str:
    if col == "req/fetched":
        return f"{row['fetched']}/{row['requested']} ({row['coverage']:.1%})"
    value = row[col]
    if col in ("total_return", "max_drawdown", "win_rate"):
        return f"{value:.2%}"
    if col in ("sharpe", "avg_pairwise_corr"):
        return f"{value:.3f}"
    if col == "num_fills":
        return str(int(value))
    return str(value)


def print_table(rows: list) -> None:
    cols = ["universe", "req/fetched", "avg_pairwise_corr", "total_return", "max_drawdown", "sharpe", "win_rate", "num_fills"]
    widths = {c: max(len(c), *(len(_fmt(r, c)) for r in rows)) for c in cols}
    print(" | ".join(c.ljust(widths[c]) for c in cols))
    print("-+-".join("-" * widths[c] for c in cols))
    for r in rows:
        print(" | ".join(_fmt(r, c).ljust(widths[c]) for c in cols))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Point-in-time (selection-bias-reduced) universe comparison harness")
    parser.add_argument("--periods", nargs="*", default=list(PERIODS.keys()), choices=list(PERIODS.keys()))
    parser.add_argument("--max-positions", type=int, default=5)
    parser.add_argument("--cash", type=float, default=100_000.0)
    args = parser.parse_args(argv)

    provider, _settings = _provider()

    # Accumulated across all periods actually run, for the overall sp500_pit
    # residual-survivorship coverage figure (distinct symbols, union across windows).
    sp500_pit_requested_union = set()
    sp500_pit_fetched_union = set()

    for period_name in args.periods:
        start, end = PERIODS[period_name]
        config = BacktestConfig(
            start=start, end=end, initial_cash=args.cash, max_positions=args.max_positions,
            min_score=0.5, slippage_bps=5.0,
        )
        print(f"\n=== {period_name}: {start.isoformat()} -> {end.isoformat()} "
              f"(cash={config.initial_cash:,.0f}, max_positions={config.max_positions}, "
              f"min_score={config.min_score}, lookback_days={config.lookback_days}, "
              f"slippage_bps={config.slippage_bps}) ===")

        universes = build_universes(start)
        rows = [run_universe(name, symbols, provider, config) for name, symbols in universes.items()]
        print_table(rows)

        for r in rows:
            if r["universe"] == "sp500_pit":
                sp500_pit_requested_union.update(universes["sp500_pit"])
                sp500_pit_fetched_union.update(r["fetched_symbols"])

    if sp500_pit_requested_union:
        req_n = len(sp500_pit_requested_union)
        fet_n = len(sp500_pit_fetched_union)
        print(f"\n=== sp500_pit overall (union across periods run) ===")
        print(f"distinct requested={req_n}, distinct fetched={fet_n}, "
              f"coverage={fet_n / req_n:.1%}, missing={req_n - fet_n} "
              f"(residual survivorship gap: names index history includes but yfinance cannot fetch)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
