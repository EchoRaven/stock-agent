"""EXPERIMENTAL stop-loss backtest engine (research-only, additive).

Mirrors BacktestEngine's daily loop (engine.py, FINAL, NOT modified) but
inserts a per-day stop-loss check after filling yesterday's orders and
before the normal rank/rebalance. Reuses SimBroker/ReplayPriceProvider/
metrics unchanged; stop_mode=None reduces to exactly BacktestEngine's
behavior (see test_stoploss_engine.py::test_no_stop_matches_baseline_engine).
"""

import datetime as dt
from dataclasses import dataclass

import pandas as pd

from app.backtest.metrics import max_drawdown, sharpe, total_return, win_rate
from app.backtest.sim_broker import Order, SimBroker
from app.data.replay import ReplayPriceProvider
from app.screener.indicators import atr as atr_indicator

STOP_MODES = (None, "fixed_pct", "atr", "trailing", "portfolio_dd")


@dataclass(frozen=True)
class StopLossConfig:
    start: dt.date
    end: dt.date
    initial_cash: float = 100_000.0
    max_positions: int = 5
    min_score: float = 0.5
    lookback_days: int = 250
    slippage_bps: float = 5.0
    stop_mode: str = None
    stop_fixed_pct: float = 0.08
    stop_atr_mult: float = 2.5
    stop_atr_window: int = 14
    stop_trailing_pct: float = 0.10
    stop_portfolio_dd_pct: float = 0.15
    stop_portfolio_pause_days: int = 5

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError("start must not be after end")
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        if self.max_positions < 1:
            raise ValueError("max_positions must be >= 1")
        if self.stop_mode not in STOP_MODES:
            raise ValueError(f"stop_mode must be one of {STOP_MODES}")


@dataclass(frozen=True)
class BacktestResult:
    equity_curve: pd.Series
    fills: list
    metrics: dict


def _triggered_fixed_pct(price: float, entry: float, pct: float) -> bool:
    return price <= entry * (1 - pct)


def _triggered_atr(price: float, entry: float, atr_value: float, k: float) -> bool:
    return price <= entry - k * atr_value


def _triggered_trailing(price: float, high_since_entry: float, pct: float) -> bool:
    return price <= high_since_entry * (1 - pct)


def _triggered_portfolio_dd(equity: float, peak_equity: float, pct: float) -> bool:
    if peak_equity <= 0:
        return False
    return (equity / peak_equity - 1) <= -pct


class StopLossEngine:
    """如 BacktestEngine,但在成交后/调仓前插入止损检查。"""

    def __init__(self, bars_by_symbol: dict, screener, config: StopLossConfig):
        self._bars = bars_by_symbol
        self._screener = screener
        self._cfg = config

    def run(self) -> BacktestResult:
        cfg = self._cfg
        calendar = self._calendar()
        if not calendar:
            raise ValueError("no trading days in backtest range")
        provider = ReplayPriceProvider(self._bars)
        broker = SimBroker(cash=cfg.initial_cash, slippage_bps=cfg.slippage_bps)
        last_close: dict = {}
        equity_points: dict = {}
        entry_price: dict = {}       # symbol -> volume-weighted avg cost
        high_since_entry: dict = {}  # symbol -> highest close since entry (trailing)
        peak_equity = cfg.initial_cash
        pause_remaining = 0
        seen_fills = 0

        for ts in calendar:
            today = ts.date()
            broker.process_fills(today, self._prices_at(ts, "open"))
            last_close.update(self._prices_at(ts, "close"))
            provider.set_as_of(today)

            seen_fills = self._update_bookkeeping(broker, entry_price, high_since_entry, seen_fills)
            for sym in broker.positions:
                if sym in last_close:
                    high_since_entry[sym] = max(high_since_entry.get(sym, last_close[sym]), last_close[sym])

            stopped, paused_today, peak_equity, pause_remaining = self._check_stops(
                cfg, broker, provider, today, last_close, entry_price, high_since_entry,
                peak_equity, pause_remaining,
            )

            start = today - dt.timedelta(days=cfg.lookback_days)
            history = {sym: provider.get_daily_bars(sym, start, today) for sym in self._bars}
            scores = self._screener.rank(history, top_n=cfg.max_positions)
            targets = [s.symbol for s in scores if s.total >= cfg.min_score]

            for sym in list(broker.positions):
                if sym not in stopped and sym not in targets:
                    broker.submit(Order(sym, "sell", broker.position(sym)))

            if not paused_today:
                budget = broker.equity(last_close) / cfg.max_positions
                for sym in targets:
                    if broker.position(sym) == 0 and sym in last_close:
                        shares = int(budget // last_close[sym])
                        if shares > 0:
                            broker.submit(Order(sym, "buy", shares))

            equity_points[ts] = broker.equity(last_close)

        equity = pd.Series(equity_points).sort_index()
        return BacktestResult(
            equity_curve=equity,
            fills=list(broker.fills),
            metrics={
                "total_return": total_return(equity),
                "max_drawdown": max_drawdown(equity),
                "sharpe": sharpe(equity),
                "win_rate": win_rate(broker.fills),
                "num_fills": float(len(broker.fills)),
            },
        )

    @staticmethod
    def _update_bookkeeping(broker, entry_price, high_since_entry, seen_fills):
        for f in broker.fills[seen_fills:]:
            if f.side == "buy":
                total_shares = broker.position(f.symbol)
                old_shares = total_shares - f.shares
                old_cost = entry_price.get(f.symbol, 0.0) * old_shares
                entry_price[f.symbol] = (old_cost + f.shares * f.price) / total_shares
                high_since_entry[f.symbol] = max(high_since_entry.get(f.symbol, f.price), f.price)
            elif broker.position(f.symbol) == 0:
                entry_price.pop(f.symbol, None)
                high_since_entry.pop(f.symbol, None)
        return len(broker.fills)

    def _check_stops(self, cfg, broker, provider, today, last_close, entry_price,
                      high_since_entry, peak_equity, pause_remaining):
        stopped: set = set()
        paused_today = pause_remaining > 0
        if cfg.stop_mode == "portfolio_dd":
            equity_now = broker.equity(last_close)
            peak_equity = max(peak_equity, equity_now)
            if pause_remaining == 0 and _triggered_portfolio_dd(equity_now, peak_equity, cfg.stop_portfolio_dd_pct):
                for sym in list(broker.positions):
                    broker.submit(Order(sym, "sell", broker.position(sym)))
                    stopped.add(sym)
                pause_remaining = cfg.stop_portfolio_pause_days
                paused_today = True
                peak_equity = equity_now  # reset high-water mark so cash-in-pause doesn't stay "underwater" forever
            if pause_remaining > 0:
                pause_remaining -= 1
        elif cfg.stop_mode in ("fixed_pct", "atr", "trailing"):
            for sym in list(broker.positions):
                price = last_close.get(sym)
                if price is None:
                    continue
                if self._position_stop_triggered(cfg, provider, sym, today, price, entry_price, high_since_entry):
                    broker.submit(Order(sym, "sell", broker.position(sym)))
                    stopped.add(sym)
        return stopped, paused_today, peak_equity, pause_remaining

    def _position_stop_triggered(self, cfg, provider, sym, today, price, entry_price, high_since_entry) -> bool:
        if cfg.stop_mode == "fixed_pct":
            entry = entry_price.get(sym)
            return entry is not None and _triggered_fixed_pct(price, entry, cfg.stop_fixed_pct)
        if cfg.stop_mode == "trailing":
            high = high_since_entry.get(sym)
            return high is not None and _triggered_trailing(price, high, cfg.stop_trailing_pct)
        if cfg.stop_mode == "atr":
            entry = entry_price.get(sym)
            atr_val = self._atr_value(provider, sym, today, cfg.stop_atr_window)
            return entry is not None and atr_val is not None and _triggered_atr(price, entry, atr_val, cfg.stop_atr_mult)
        return False

    def _atr_value(self, provider, sym, today, window):
        start = today - dt.timedelta(days=max(window * 3, 30))
        bars = provider.get_daily_bars(sym, start, today)
        if len(bars) < window + 1:
            return None
        val = atr_indicator(bars, window).iloc[-1]
        return None if pd.isna(val) else float(val)

    def _calendar(self) -> list:
        dates = set()
        for df in self._bars.values():
            for ts in df.index:
                if self._cfg.start <= ts.date() <= self._cfg.end:
                    dates.add(ts)
        return sorted(dates)

    def _prices_at(self, ts, column: str) -> dict:
        out = {}
        for sym, df in self._bars.items():
            if ts in df.index:
                out[sym] = float(df.at[ts, column])
        return out
