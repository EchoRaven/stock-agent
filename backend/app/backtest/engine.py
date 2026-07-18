import datetime as dt
from dataclasses import dataclass

import pandas as pd

from app.backtest.metrics import max_drawdown, sharpe, total_return, win_rate
from app.backtest.sim_broker import Order, SimBroker
from app.data.replay import ReplayPriceProvider


@dataclass(frozen=True)
class BacktestConfig:
    start: dt.date
    end: dt.date
    initial_cash: float = 100_000.0
    max_positions: int = 5
    min_score: float = 0.5
    lookback_days: int = 250
    slippage_bps: float = 5.0


@dataclass(frozen=True)
class BacktestResult:
    equity_curve: pd.Series
    fills: list
    metrics: dict


class BacktestEngine:
    """日线事件循环:T 日收盘后用 ≤T 数据决策,订单 T+1 开盘成交。"""

    def __init__(self, bars_by_symbol: dict, screener, config: BacktestConfig):
        self._bars = bars_by_symbol
        self._screener = screener  # 只依赖 .rank(bars_by_symbol, top_n)
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

        for ts in calendar:
            today = ts.date()
            broker.process_fills(today, self._prices_at(ts, "open"))
            last_close.update(self._prices_at(ts, "close"))

            provider.set_as_of(today)
            start = today - dt.timedelta(days=cfg.lookback_days)
            history = {sym: provider.get_daily_bars(sym, start, today) for sym in self._bars}
            scores = self._screener.rank(history, top_n=cfg.max_positions)
            targets = [s.symbol for s in scores if s.total >= cfg.min_score]

            for sym in list(broker.positions):
                if sym not in targets:
                    broker.submit(Order(sym, "sell", broker.position(sym)))

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
