"""Objective backtest comparison of market-regime overlay variants.

Research-only harness for the market-regime experiment: does a SPY-200SMA
regime overlay cut drawdown in bear markets without wrecking bull returns?
Tested across three fixed windows -- a strong bull, a choppy/weak window,
and a genuine bear market (2022, SPY spent long stretches under its 200SMA)
-- using identical params in every window to avoid overfitting to any one
regime. Mirrors scripts/stoploss_experiment.py's structure (same
_provider()/fetch_period_bars()/run_variant()/table-printer shape, same
DEFAULT_UNIVERSE + default_screener weights) but compares BacktestEngine
(baseline, no regime filter) against RegimeBacktestEngine running each
regime_mode, using the SAME default_screener for every variant -- only the
regime gate changes, not the screener.

NOT part of the offline pytest suite (lives under scripts/, outside
pytest's testpaths=["tests"]) and requires network (yfinance, cached via
data_cache/, same as stoploss_experiment.py / strategy_experiment.py).

Usage:
    uv run python -m scripts.regime_experiment
    uv run python -m scripts.regime_experiment --periods bear_2022
"""

import argparse
import datetime as dt
import sys

from app.backtest.engine import BacktestConfig, BacktestEngine
from app.backtest.regime_engine import RegimeBacktestEngine, RegimeConfig
from app.backtest.regime_signal import risk_on_asof
from app.config import get_settings
from app.data.cache import CachedPriceProvider
from app.data.prices_yfinance import YFinancePriceProvider
from app.screener.universe import DEFAULT_UNIVERSE
from app.services.analysis_service import default_screener
from app.services.market_data_service import fetch_bars

# Three fixed, reproducible windows -- bull / chop / a genuine bear market.
PERIODS = {
    "bull_2023H2_2024H1": (dt.date(2023, 7, 1), dt.date(2024, 6, 30)),   # 基线很强(+30%)
    "chop_2024H2_2025H1": (dt.date(2024, 7, 1), dt.date(2025, 6, 30)),   # 基线偏弱(+3%)
    "bear_2022":          (dt.date(2022, 1, 1), dt.date(2022, 12, 31)),  # 真熊市:SPY 大段在 200 均线下 —— regime 的真正考场
    "whipsaw_covid_2020": (dt.date(2020, 1, 1), dt.date(2020, 12, 31)),  # V 型:2-3月暴跌后急速反弹 —— 200SMA 过滤器的对抗性最差场景(可能卖在底、追在高)
}

# variant name -> RegimeConfig kwargs override (None = baseline BacktestEngine, no regime gate)
REGIME_VARIANTS = {
    "baseline (no regime)":       None,
    "regime_flat (SPY<200SMA→现金)":   dict(regime_mode="flat",   regime_sma_window=200),
    "regime_no_new (SPY<200SMA→不新开)": dict(regime_mode="no_new", regime_sma_window=200),
}

INDEX_SYMBOL = "SPY"
SPY_LOOKBACK_DAYS = 400  # deeper than the universe lookback so the 200SMA is defined from day 1 of the window


def _provider():
    settings = get_settings()
    return CachedPriceProvider(YFinancePriceProvider(), settings.cache_dir), settings


def fetch_period_bars(provider, symbols, start, end, lookback_days) -> dict:
    fetch_start = start - dt.timedelta(days=lookback_days)
    bars, skipped = fetch_bars(provider, symbols, fetch_start, end)
    if skipped:
        print(f"[warn] skipped {len(skipped)}: {skipped}")
    return bars


def fetch_spy_bars(provider, start, end):
    """SPY history for the regime signal, fetched from well before the window
    so the 200SMA is defined from day 1. Returns None (with a warning) if
    the fetch fails or comes back empty -- callers must SKIP the period
    rather than silently run with no regime data."""
    spy_start = start - dt.timedelta(days=SPY_LOOKBACK_DAYS)
    bars, skipped = fetch_bars(provider, [INDEX_SYMBOL], spy_start, end)
    if skipped:
        print(f"[warn] SPY fetch skipped: {skipped}")
    return bars.get(INDEX_SYMBOL)


def run_variant(name: str, regime_kwargs, bars_by_symbol: dict, spy_bars, base_cfg: BacktestConfig) -> dict:
    if regime_kwargs is None:
        result = BacktestEngine(bars_by_symbol, default_screener(), base_cfg).run()
    else:
        cfg = RegimeConfig(
            start=base_cfg.start, end=base_cfg.end, initial_cash=base_cfg.initial_cash,
            max_positions=base_cfg.max_positions, min_score=base_cfg.min_score,
            lookback_days=base_cfg.lookback_days, slippage_bps=base_cfg.slippage_bps,
            **regime_kwargs,
        )
        result = RegimeBacktestEngine(bars_by_symbol, default_screener(), cfg, index_bars=spy_bars).run()
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


def spy_buy_and_hold_return(spy_bars, start, end) -> float:
    """SPY close-to-close buy&hold return over [start, end] (bars already
    include the pre-window lookback, so filter to the window first)."""
    window = spy_bars["close"][(spy_bars.index.date >= start) & (spy_bars.index.date <= end)]
    if len(window) < 2:
        return float("nan")
    return float(window.iloc[-1] / window.iloc[0] - 1.0)


def risk_off_fraction(spy_bars, start, end, sma_window: int = 200) -> float:
    """Fraction of the window's SPY trading days that were risk-off, per risk_on_asof."""
    days = sorted({ts.date() for ts in spy_bars.index if start <= ts.date() <= end})
    if not days:
        return float("nan")
    risk_off_days = sum(1 for day in days if not risk_on_asof(spy_bars, day, sma_window))
    return risk_off_days / len(days)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Market-regime overlay comparison harness")
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

        spy_bars = fetch_spy_bars(provider, start, end)
        if spy_bars is None or spy_bars.empty:
            print(f"[warn] no SPY data for {period_name} -- SKIPPING period (regime signal undefined)")
            continue

        spy_return = spy_buy_and_hold_return(spy_bars, start, end)
        risk_off_frac = risk_off_fraction(spy_bars, start, end, sma_window=200)
        print(f"SPY 区间收益: {spy_return:+.2%}; risk-off 天数占比: {risk_off_frac:.2%}")

        bars = fetch_period_bars(provider, symbols, start, end, config.lookback_days)

        rows = [run_variant(name, kwargs, bars, spy_bars, config) for name, kwargs in REGIME_VARIANTS.items()]
        print_table(rows)

    return 0


if __name__ == "__main__":
    sys.exit(main())
