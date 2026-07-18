import datetime as dt

import pytest

from app.backtest.engine import BacktestConfig, BacktestEngine
from app.screener.base import SymbolScore
from tests.helpers import make_bars


class ScriptedScreener:
    """第 i 次 rank 返回脚本第 i 项选股(score=1.0);脚本耗尽沿用最后一项。"""

    def __init__(self, picks):
        self.picks = picks
        self.calls = 0

    def rank(self, bars_by_symbol, top_n):
        pick = self.picks[min(self.calls, len(self.picks) - 1)]
        self.calls += 1
        if pick is None or pick not in bars_by_symbol:
            return []
        return [SymbolScore(pick, 1.0, {})]


def _bars():
    return {
        "AAA": make_bars(start="2024-01-01", days=10, base=100.0),
        "BBB": make_bars(start="2024-01-01", days=10, base=50.0),
    }


def _cfg(**kw):
    defaults = dict(
        start=dt.date(2024, 1, 1),
        end=dt.date(2024, 1, 12),
        initial_cash=10_000.0,
        max_positions=1,
        min_score=0.5,
        lookback_days=30,
        slippage_bps=0.0,
    )
    defaults.update(kw)
    return BacktestConfig(**defaults)


def test_buys_pick_next_open():
    result = BacktestEngine(_bars(), ScriptedScreener(["AAA"]), _cfg()).run()
    assert result.fills
    f = result.fills[0]
    assert (f.symbol, f.side) == ("AAA", "buy")
    assert f.date == dt.date(2024, 1, 2)  # T 日决策,T+1 开盘成交
    # 决策日收盘 100 → 目标 100 股;次日开盘 100.5,现金只够 99 股
    assert f.shares == 99
    assert f.price == pytest.approx(100.5)


def test_equity_curve_tracks_position():
    result = BacktestEngine(_bars(), ScriptedScreener(["AAA"]), _cfg()).run()
    eq = result.equity_curve
    assert len(eq) == 10
    assert eq.iloc[0] == pytest.approx(10_000.0)  # 首日只挂单未成交
    # 期末:现金 10000-99*100.5=50.5,持仓 99 股 × 期末收盘 109
    assert eq.iloc[-1] == pytest.approx(50.5 + 99 * 109.0)
    for key in ("total_return", "max_drawdown", "sharpe", "win_rate", "num_fills"):
        assert key in result.metrics


def test_sells_when_dropped_from_targets():
    picks = ["AAA", "AAA", "BBB"]
    result = BacktestEngine(_bars(), ScriptedScreener(picks), _cfg()).run()
    sides = [(f.symbol, f.side) for f in result.fills]
    assert ("AAA", "buy") in sides
    assert ("AAA", "sell") in sides
    assert ("BBB", "buy") in sides


def test_no_trades_when_below_min_score():
    result = BacktestEngine(_bars(), ScriptedScreener([None]), _cfg()).run()
    assert result.fills == []
    assert result.equity_curve.iloc[-1] == pytest.approx(10_000.0)


def test_empty_range_raises():
    with pytest.raises(ValueError):
        BacktestEngine(_bars(), ScriptedScreener(["AAA"]),
                       _cfg(start=dt.date(2030, 1, 1), end=dt.date(2030, 1, 5))).run()
