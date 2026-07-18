import datetime as dt

import pandas as pd

from app.backtest.engine import BacktestConfig, BacktestResult
from app.report.markdown import render_backtest_report, render_screen_report
from app.screener.base import RuleResult, SymbolScore


def test_render_screen_report():
    scores = [
        SymbolScore("AAPL", 0.85, {"trend": RuleResult(1.0, "all up"), "volume": RuleResult(0.5, "ok")}),
        SymbolScore("MSFT", 0.60, {"trend": RuleResult(0.6, "mixed")}),
    ]
    text = render_screen_report(scores, dt.date(2026, 7, 17))
    assert "2026-07-17" in text
    assert "AAPL" in text and "MSFT" in text
    assert "0.850" in text
    assert "all up" in text


def test_render_backtest_report():
    result = BacktestResult(
        equity_curve=pd.Series([100.0, 110.0]),
        fills=[],
        metrics={"total_return": 0.10, "max_drawdown": -0.05, "sharpe": 1.5,
                 "win_rate": 0.6, "num_fills": 4.0},
    )
    config = BacktestConfig(start=dt.date(2024, 1, 1), end=dt.date(2024, 6, 30))
    text = render_backtest_report(result, config)
    assert "2024-01-01" in text and "2024-06-30" in text
    assert "10.00%" in text   # 总收益
    assert "-5.00%" in text   # 最大回撤
    assert "1.50" in text     # 夏普
