import datetime as dt

import pytest

from app.backtest.engine import BacktestConfig, BacktestEngine
from app.backtest.stoploss_engine import (
    StopLossConfig,
    StopLossEngine,
    _triggered_atr,
    _triggered_fixed_pct,
    _triggered_portfolio_dd,
    _triggered_trailing,
)
from app.screener.base import SymbolScore
from tests.helpers import make_bars


class ScriptedScreener:
    """第 i 次 rank 返回脚本第 i 项选股(score=1.0);脚本耗尽沿用最后一项。
    item 可以是单个 symbol、symbol 列表,或 None(不选)。"""

    def __init__(self, picks):
        self.picks = picks
        self.calls = 0

    def rank(self, bars_by_symbol, top_n):
        pick = self.picks[min(self.calls, len(self.picks) - 1)]
        self.calls += 1
        if pick is None:
            return []
        syms = pick if isinstance(pick, list) else [pick]
        return [SymbolScore(s, 1.0, {}) for s in syms if s in bars_by_symbol][:top_n]


def bars_from_closes(closes, start="2024-01-01"):
    """构造自定义收盘价路径的日线:open=close-0.5,high=close+1,low=close-1。"""
    import numpy as np
    import pandas as pd

    idx = pd.bdate_range(start, periods=len(closes))
    close = pd.Series([float(c) for c in closes], index=idx)
    return pd.DataFrame(
        {
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1_000_000.0,
        },
        index=idx,
    )


def _cfg(**kw):
    defaults = dict(
        start=dt.date(2024, 1, 1),
        end=dt.date(2024, 2, 1),
        initial_cash=10_000.0,
        max_positions=1,
        min_score=0.5,
        lookback_days=30,
        slippage_bps=0.0,
    )
    defaults.update(kw)
    return StopLossConfig(**defaults)


# ---------------------------------------------------------------------------
# Pure trigger-predicate unit tests: exact boundary behavior of each stop rule
# ---------------------------------------------------------------------------


def test_fixed_pct_boundary():
    assert _triggered_fixed_pct(price=92.0, entry=100.0, pct=0.08) is True  # exactly -8%
    assert _triggered_fixed_pct(price=92.01, entry=100.0, pct=0.08) is False


def test_atr_boundary():
    # entry=100, atr=4, k=2.5 -> stop level = 100 - 10 = 90
    assert _triggered_atr(price=90.0, entry=100.0, atr_value=4.0, k=2.5) is True
    assert _triggered_atr(price=90.01, entry=100.0, atr_value=4.0, k=2.5) is False


def test_trailing_boundary():
    assert _triggered_trailing(price=108.0, high_since_entry=120.0, pct=0.10) is True
    assert _triggered_trailing(price=108.01, high_since_entry=120.0, pct=0.10) is False


def test_portfolio_dd_boundary():
    assert _triggered_portfolio_dd(equity=85.0, peak_equity=100.0, pct=0.15) is True
    assert _triggered_portfolio_dd(equity=85.01, peak_equity=100.0, pct=0.15) is False
    assert _triggered_portfolio_dd(equity=85.0, peak_equity=0.0, pct=0.15) is False


# ---------------------------------------------------------------------------
# Parity: with stop_mode=None the engine must reproduce BacktestEngine exactly
# ---------------------------------------------------------------------------


def _bars():
    return {
        "AAA": make_bars(start="2024-01-01", days=15, base=100.0),
        "BBB": make_bars(start="2024-01-01", days=15, base=50.0),
    }


def test_no_stop_matches_baseline_engine():
    picks = ["AAA", "AAA", "BBB"]
    bars = _bars()
    base = BacktestEngine(bars, ScriptedScreener(picks), BacktestConfig(
        start=dt.date(2024, 1, 1), end=dt.date(2024, 1, 20), initial_cash=10_000.0,
        max_positions=1, min_score=0.5, lookback_days=30, slippage_bps=0.0,
    )).run()
    exp = StopLossEngine(bars, ScriptedScreener(picks), _cfg(
        end=dt.date(2024, 1, 20), stop_mode=None,
    )).run()
    assert (exp.equity_curve == base.equity_curve).all()
    assert exp.fills == base.fills
    assert exp.metrics == base.metrics


# ---------------------------------------------------------------------------
# End-to-end: fixed_pct actually submits & fills an early sell on a crash
# ---------------------------------------------------------------------------


def test_fixed_pct_triggers_on_crash_not_on_flat():
    # Day0 close=100 (buys next open ~99.5); then flat at 100 for a while,
    # then a crash to 80 (-20%, past an 8% stop) that a baseline engine
    # (which keeps recommending AAA forever) would never sell on its own.
    closes = [100] * 4 + [80] * 6
    bars = {"AAA": bars_from_closes(closes)}
    cfg = _cfg(end=dt.date(2024, 1, 31), stop_mode="fixed_pct", stop_fixed_pct=0.08)
    result = StopLossEngine(bars, ScriptedScreener(["AAA"]), cfg).run()
    sell_fills = [f for f in result.fills if f.side == "sell"]
    assert sell_fills, "expected the crash to trigger a stop-loss sell"
    # baseline (no stop) never sells because ScriptedScreener always picks AAA
    base = StopLossEngine(bars, ScriptedScreener(["AAA"]), _cfg(
        end=dt.date(2024, 1, 31), stop_mode=None,
    )).run()
    assert not [f for f in base.fills if f.side == "sell"]


def test_fixed_pct_does_not_trigger_on_mild_dip():
    # -3% dip stays above an 8% stop -> no stop sell fired.
    closes = [100] * 4 + [97] * 6
    bars = {"AAA": bars_from_closes(closes)}
    cfg = _cfg(end=dt.date(2024, 1, 31), stop_mode="fixed_pct", stop_fixed_pct=0.08)
    result = StopLossEngine(bars, ScriptedScreener(["AAA"]), cfg).run()
    assert not [f for f in result.fills if f.side == "sell"]


# ---------------------------------------------------------------------------
# End-to-end: trailing stop cares about the peak since entry, not entry price
# ---------------------------------------------------------------------------


def test_trailing_triggers_on_pullback_from_peak():
    # Runs up to 130 (new peak), then pulls back 12% from that peak (>10% stop)
    # even though it's still above the original entry price.
    closes = [100, 110, 120, 130, 130, 114, 114, 114]
    bars = {"AAA": bars_from_closes(closes)}
    cfg = _cfg(end=dt.date(2024, 1, 31), stop_mode="trailing", stop_trailing_pct=0.10)
    result = StopLossEngine(bars, ScriptedScreener(["AAA"]), cfg).run()
    assert [f for f in result.fills if f.side == "sell"]


def test_trailing_does_not_trigger_on_pure_uptrend():
    closes = [100, 105, 110, 115, 120, 125, 130, 135]
    bars = {"AAA": bars_from_closes(closes)}
    cfg = _cfg(end=dt.date(2024, 1, 31), stop_mode="trailing", stop_trailing_pct=0.10)
    result = StopLossEngine(bars, ScriptedScreener(["AAA"]), cfg).run()
    assert not [f for f in result.fills if f.side == "sell"]


# ---------------------------------------------------------------------------
# End-to-end: portfolio_dd liquidates ALL holdings and pauses new buys
# ---------------------------------------------------------------------------


def test_portfolio_dd_liquidates_all_and_pauses_buys():
    aaa = [100] * 4 + [60] * 16  # crashes hard -> breaches portfolio dd
    bbb = [50] * 20
    bars = {"AAA": bars_from_closes(aaa), "BBB": bars_from_closes(bbb)}
    trading_days = list(bars["AAA"].index.date)
    # picks AAA+BBB together so both are held when the drawdown hits
    cfg = _cfg(
        end=trading_days[-1], max_positions=2,
        stop_mode="portfolio_dd", stop_portfolio_dd_pct=0.15, stop_portfolio_pause_days=3,
    )
    result = StopLossEngine(bars, ScriptedScreener([["AAA", "BBB"]]), cfg).run()
    sells = [f for f in result.fills if f.side == "sell"]
    assert sells, "expected a liquidation sell after the portfolio dd breach"
    liq_day = min(f.date for f in sells)
    liq_symbols = {f.symbol for f in sells if f.date == liq_day}
    assert liq_symbols == {"AAA", "BBB"}  # both liquidated together, same day

    liq_idx = trading_days.index(liq_day)
    pause_end_idx = liq_idx + cfg.stop_portfolio_pause_days - 1
    pause_window = set(trading_days[liq_idx:pause_end_idx + 1])
    buys_in_pause = [f for f in result.fills if f.side == "buy" and f.date in pause_window]
    assert buys_in_pause == []

    buys_after_pause = [f for f in result.fills if f.side == "buy" and f.date > trading_days[pause_end_idx]]
    assert buys_after_pause, "expected buys to resume once the pause window ends"


def test_config_validation_reused():
    with pytest.raises(ValueError):
        _cfg(max_positions=0)
    with pytest.raises(ValueError):
        _cfg(stop_mode="not_a_real_mode")
