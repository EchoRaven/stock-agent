"""EXPERIMENTAL 市场状态(regime)回测引擎(research-only,additive)。
镜像 BacktestEngine(engine.py,FINAL,未改)的日循环,在调仓前加一个大盘
regime 闸门。regime_mode=None → 与 BacktestEngine 逐步等价(见等价测试)。
复用 SimBroker/ReplayPriceProvider/metrics/risk_on_asof。"""
import datetime as dt
from dataclasses import dataclass

import pandas as pd

from app.backtest.metrics import max_drawdown, sharpe, total_return, win_rate
from app.backtest.regime_signal import risk_on_asof
from app.backtest.sim_broker import Order, SimBroker
from app.data.replay import ReplayPriceProvider

REGIME_MODES = (None, "flat", "no_new")


@dataclass(frozen=True)
class RegimeConfig:
    start: dt.date
    end: dt.date
    initial_cash: float = 100_000.0
    max_positions: int = 5
    min_score: float = 0.5
    lookback_days: int = 250
    slippage_bps: float = 5.0
    regime_mode: str = None          # None=无过滤; "flat"=风险关时清仓且不新开; "no_new"=风险关时只是不新开(留存量)
    regime_sma_window: int = 200

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError("start must not be after end")
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        if self.max_positions < 1:
            raise ValueError("max_positions must be >= 1")
        if self.regime_mode not in REGIME_MODES:
            raise ValueError(f"regime_mode must be one of {REGIME_MODES}")


@dataclass(frozen=True)
class BacktestResult:
    equity_curve: pd.Series
    fills: list
    metrics: dict


class RegimeBacktestEngine:
    """如 BacktestEngine,但调仓前用大盘 regime 决定是否新开/清仓。
    index_bars 仅用于信号(不参与交易/不进 universe)。"""

    def __init__(self, bars_by_symbol: dict, screener, config: RegimeConfig,
                 index_bars: pd.DataFrame = None):
        self._bars = bars_by_symbol
        self._screener = screener
        self._cfg = config
        self._index_bars = index_bars

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

            # regime 闸门(防未来函数:只用 <= today 的指数数据)
            risk_on = True
            if cfg.regime_mode is not None:
                risk_on = risk_on_asof(self._index_bars, today, cfg.regime_sma_window)

            # 卖出:不在目标里的持仓正常卖;若 mode=="flat" 且 risk-off,全部清仓
            for sym in list(broker.positions):
                if (cfg.regime_mode == "flat" and not risk_on) or sym not in targets:
                    broker.submit(Order(sym, "sell", broker.position(sym)))

            # 买入:仅在 risk-on 时新开(risk-off 一律不新开,无论 flat/no_new)
            if risk_on:
                budget = broker.equity(last_close) / cfg.max_positions
                for sym in targets:
                    if broker.position(sym) == 0 and sym in last_close:
                        shares = int(budget // last_close[sym])
                        if shares > 0:
                            broker.submit(Order(sym, "buy", shares))

            equity_points[ts] = broker.equity(last_close)

        equity = pd.Series(equity_points).sort_index()
        return BacktestResult(
            equity_curve=equity, fills=list(broker.fills),
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
