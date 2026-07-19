"""Objective backtest comparison of universe breadth/correlation variants.

Research-only harness for the universe-diversification experiment: the
baseline's weakness was diagnosed as "30 high-correlation mega-caps move
together." Does running the SAME baseline strategy (same default_screener,
same BacktestConfig) on a larger, sector-diversified, lower-correlation
universe improve risk-adjusted returns (esp. max drawdown / Sharpe)? Tested
across four fixed windows (bull / chop / bear / COVID whipsaw), same windows
as scripts/regime_experiment.py.

Only the universe changes between variants -- everything else (screener
weights, cash, max_positions, min_score, slippage, lookback) is held
identical, to isolate the effect of universe breadth/correlation. A
correlation metric (average pairwise correlation of in-window daily returns)
is printed for each universe/period to confirm the diversified universe is
genuinely lower-correlation before looking at the backtest metrics.

NOT part of the offline pytest suite (lives under scripts/, outside
pytest's testpaths=["tests"]) and requires network (yfinance, cached via
data_cache/, same as regime_experiment.py / stoploss_experiment.py).

Usage:
    uv run python -m scripts.universe_experiment
    uv run python -m scripts.universe_experiment --periods bear_2022
"""

import argparse
import datetime as dt
import sys

from app.backtest.engine import BacktestConfig, BacktestEngine
from app.config import get_settings
from app.data.cache import CachedPriceProvider
from app.data.prices_yfinance import YFinancePriceProvider
from app.screener.universe import DEFAULT_UNIVERSE  # 30-name control
from app.services.analysis_service import default_screener
from app.services.market_data_service import fetch_bars

# ~50 names deliberately spread across 11 sectors, adding defensive/low-beta
# sectors (utilities, staples, healthcare, REITs, materials) that tend to be
# less correlated with the mega-cap tech that dominates DEFAULT_UNIVERSE.
DIVERSIFIED_UNIVERSE = [
    # Tech (trimmed vs default)
    "AAPL", "MSFT", "NVDA", "CRM", "CSCO",
    # Communication services
    "GOOGL", "META", "NFLX", "DIS", "VZ", "T",
    # Consumer discretionary
    "AMZN", "TSLA", "HD", "MCD", "NKE",
    # Consumer staples
    "PG", "KO", "PEP", "WMT", "COST", "CL",
    # Financials
    "JPM", "BAC", "V", "MA", "GS", "WFC",
    # Healthcare
    "UNH", "LLY", "JNJ", "ABBV", "MRK", "PFE",
    # Energy
    "XOM", "CVX", "COP", "SLB",
    # Industrials
    "CAT", "HON", "UNP", "GE", "RTX",
    # Utilities
    "NEE", "DUK", "SO", "AEP",
    # Materials
    "LIN", "FCX", "NEM", "SHW",
    # Real estate
    "AMT", "PLD", "O",
]

UNIVERSES = {"default_30": list(DEFAULT_UNIVERSE), "diversified_54": DIVERSIFIED_UNIVERSE}

# Same four fixed, reproducible windows as scripts/regime_experiment.py.
PERIODS = {
    "bull_2023H2_2024H1": (dt.date(2023, 7, 1), dt.date(2024, 6, 30)),
    "chop_2024H2_2025H1": (dt.date(2024, 7, 1), dt.date(2025, 6, 30)),
    "bear_2022":          (dt.date(2022, 1, 1), dt.date(2022, 12, 31)),
    "whipsaw_covid_2020": (dt.date(2020, 1, 1), dt.date(2020, 12, 31)),
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


def avg_pairwise_correlation(bars_by_symbol: dict, start: dt.date, end: dt.date) -> tuple:
    """Average pairwise Pearson correlation of in-window daily close-to-close
    returns, across all symbols with >=2 in-window bars. Returns
    (avg_corr, n_effective_symbols); avg_corr is NaN if fewer than two
    symbols have usable return series or all pairwise correlations are NaN."""
    returns = {}
    for symbol, df in bars_by_symbol.items():
        window = df[(df.index.date >= start) & (df.index.date <= end)]
        pct = window["close"].pct_change().dropna()
        if not pct.empty:
            returns[symbol] = pct
    n_effective = len(returns)
    if n_effective < 2:
        return float("nan"), n_effective

    import pandas as pd

    ret_df = pd.DataFrame(returns)
    corr = ret_df.corr()
    cols = list(corr.columns)
    pairwise = []
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            value = corr.iloc[i, j]
            if pd.notna(value):
                pairwise.append(float(value))
    avg_corr = sum(pairwise) / len(pairwise) if pairwise else float("nan")
    return avg_corr, n_effective


def run_universe(name: str, symbols: list, provider, base_cfg: BacktestConfig) -> dict:
    bars = fetch_period_bars(provider, symbols, base_cfg.start, base_cfg.end, base_cfg.lookback_days)
    avg_corr, n_effective = avg_pairwise_correlation(bars, base_cfg.start, base_cfg.end)
    print(f"[{name}] 平均两两相关: {avg_corr:.3f} (基于 {n_effective} 只有效标的)")

    result = BacktestEngine(bars, default_screener(), base_cfg).run()
    row = {"universe": name, "avg_pairwise_corr": avg_corr}
    row.update(result.metrics)
    return row


def _fmt(value, col: str) -> str:
    if col in ("total_return", "max_drawdown", "win_rate"):
        return f"{value:.2%}"
    if col in ("sharpe", "avg_pairwise_corr"):
        return f"{value:.3f}"
    if col == "num_fills":
        return str(int(value))
    return str(value)


def print_table(rows: list) -> None:
    cols = ["universe", "avg_pairwise_corr", "total_return", "max_drawdown", "sharpe", "win_rate", "num_fills"]
    widths = {c: max(len(c), *(len(_fmt(r[c], c)) for r in rows)) for c in cols}
    print(" | ".join(c.ljust(widths[c]) for c in cols))
    print("-+-".join("-" * widths[c] for c in cols))
    for r in rows:
        print(" | ".join(_fmt(r[c], c).ljust(widths[c]) for c in cols))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Universe breadth/correlation comparison harness")
    parser.add_argument("--periods", nargs="*", default=list(PERIODS.keys()), choices=list(PERIODS.keys()))
    parser.add_argument("--max-positions", type=int, default=5)
    parser.add_argument("--cash", type=float, default=100_000.0)
    args = parser.parse_args(argv)

    provider, _settings = _provider()

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

        rows = [run_universe(name, symbols, provider, config) for name, symbols in UNIVERSES.items()]
        print_table(rows)

    return 0


if __name__ == "__main__":
    sys.exit(main())
