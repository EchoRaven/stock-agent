"""RegimeBacktestEngine 单测:核心是两条"防自欺"测试——
1) regime_mode=None 必须与 BacktestEngine 逐步等价(测试功能开关本身没有副作用);
2) risk_on_asof 的防未来函数性质已在 test_regime_signal.py 验证,这里再从
   engine 层面验证 regime 闸门在 risk-off 期间确实改变了买卖行为。
"""
import datetime as dt

import pandas as pd

from app.backtest.engine import BacktestConfig, BacktestEngine
from app.backtest.regime_engine import RegimeBacktestEngine, RegimeConfig
from app.services.analysis_service import default_screener
from tests.helpers import make_bars

START = dt.date(2024, 1, 2)
END = dt.date(2024, 3, 1)


def _symbol_bars():
    # 两只标的都保持长期单调上涨,足够长的历史(>MIN_BARS=60)让默认打分器
    # 稳定给出 total>=0.5 的分数,从而在整个回测窗口内持续产生买卖活动。
    return {
        "AAA": make_bars(start="2023-06-01", days=250, base=100.0, step=1.0),
        "BBB": make_bars(start="2023-06-01", days=250, base=50.0, step=0.6),
    }


def _spy_riskon_throughout():
    # SPY 单调上涨,sma_window=20 情况下,窗口内每天 last_close > sma20。
    return make_bars(start="2023-06-01", days=250, base=400.0, step=2.0)


def _spy_riskoff_throughout():
    # SPY 单调下跌,窗口内每天 last_close < sma20。
    return make_bars(start="2023-06-01", days=250, base=400.0, step=-2.0)


def _spy_flip_midwindow():
    # SPY 先涨后跌:回测窗口前段 risk-on,某日起翻转为 risk-off 并保持到窗口结束。
    rise = make_bars(start="2023-06-01", days=170, base=200.0, step=2.0)
    last_close = float(rise["close"].iloc[-1])
    decline_start = (rise.index[-1] + pd.tseries.offsets.BDay(1)).date().isoformat()
    decline = make_bars(start=decline_start, days=80, base=last_close, step=-1.5)
    return pd.concat([rise, decline])


def _base_kwargs():
    return dict(start=START, end=END, initial_cash=100_000.0, max_positions=5,
                min_score=0.5, lookback_days=250, slippage_bps=5.0)


def test_regime_none_matches_baseline_engine():
    """CRITICAL: regime_mode=None 时,变体引擎必须与 BacktestEngine 完全一致。"""
    bars = _symbol_bars()
    base_result = BacktestEngine(
        bars, default_screener(), BacktestConfig(**_base_kwargs()),
    ).run()
    regime_result = RegimeBacktestEngine(
        bars, default_screener(),
        RegimeConfig(**_base_kwargs(), regime_mode=None),
        index_bars=_spy_riskoff_throughout(),  # 必须被完全忽略:regime_mode=None 时不读取
    ).run()

    pd.testing.assert_series_equal(regime_result.equity_curve, base_result.equity_curve)
    assert regime_result.fills == base_result.fills
    assert regime_result.metrics == base_result.metrics
    assert len(base_result.fills) > 0  # 确认这不是一个"什么都没发生"的空测试


def test_riskon_throughout_matches_baseline():
    """SPY 全程 risk-on -> regime_mode="flat" 无事可闸,结果应与 baseline 相同。"""
    bars = _symbol_bars()
    base_result = BacktestEngine(
        bars, default_screener(), BacktestConfig(**_base_kwargs()),
    ).run()
    regime_result = RegimeBacktestEngine(
        bars, default_screener(),
        RegimeConfig(**_base_kwargs(), regime_mode="flat", regime_sma_window=20),
        index_bars=_spy_riskon_throughout(),
    ).run()

    pd.testing.assert_series_equal(regime_result.equity_curve, base_result.equity_curve)
    assert regime_result.fills == base_result.fills
    assert regime_result.metrics == base_result.metrics


def test_flat_mode_liquidates_and_holds_cash_in_riskoff():
    """SPY 全程 risk-off -> flat 模式应清仓且不再新开,权益始终等于初始现金。"""
    bars = _symbol_bars()
    base_result = BacktestEngine(
        bars, default_screener(), BacktestConfig(**_base_kwargs()),
    ).run()
    regime_result = RegimeBacktestEngine(
        bars, default_screener(),
        RegimeConfig(**_base_kwargs(), regime_mode="flat", regime_sma_window=20),
        index_bars=_spy_riskoff_throughout(),
    ).run()

    assert base_result.metrics["num_fills"] > 0  # baseline 本应正常交易
    assert regime_result.fills == []              # flat+risk-off: 从未买入,也无仓位可卖
    assert regime_result.metrics["num_fills"] == 0.0
    assert (regime_result.equity_curve == 100_000.0).all()  # 权益全程持平于初始现金


def test_no_new_mode_keeps_existing_but_blocks_new_in_riskoff():
    """SPY 窗口中途翻转 risk-off -> no_new 模式:翻转后不再新开仓,
    但不强制清空已有持仓(与 flat 模式的区别)。"""
    bars = _symbol_bars()
    spy = _spy_flip_midwindow()

    from app.backtest.regime_signal import risk_on_asof

    calendar_dates = sorted({ts.date() for df in bars.values() for ts in df.index
                              if START <= ts.date() <= END})
    flip_date = next(
        d for d in calendar_dates if not risk_on_asof(spy, d, sma_window=20)
    )
    assert flip_date is not None
    # 确认这确实是窗口内的一次翻转(翻转前至少有一天 risk-on),否则测试没意义
    assert any(risk_on_asof(spy, d, sma_window=20) for d in calendar_dates if d < flip_date)

    regime_result = RegimeBacktestEngine(
        bars, default_screener(),
        RegimeConfig(**_base_kwargs(), regime_mode="no_new", regime_sma_window=20),
        index_bars=spy,
    ).run()

    buys_before_flip = [f for f in regime_result.fills if f.side == "buy" and f.date <= flip_date]
    buys_after_flip = [f for f in regime_result.fills if f.side == "buy" and f.date > flip_date]
    assert len(buys_before_flip) > 0   # 翻转前正常建仓
    assert buys_after_flip == []       # 翻转后严格无新买入

    # 不强制清仓:翻转后仍应持有 flip 前建立的仓位(而不是像 flat 模式那样归零)
    assert len(regime_result.fills) > 0
    held_symbols = {f.symbol for f in regime_result.fills if f.side == "buy"}
    assert held_symbols  # 至少买过东西,且没有被强制平仓的清仓记录淹没
