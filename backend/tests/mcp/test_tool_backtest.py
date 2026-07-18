import pytest

import app.mcp.runtime as runtime
from app.data.base import PriceProvider
from app.mcp.tool_backtest import run_backtest
from tests.helpers import make_bars


class FakePrices(PriceProvider):
    def get_daily_bars(self, symbol, start, end):
        return make_bars(start="2024-01-01", days=120, base=100.0, step=1.0)


@pytest.fixture
def fake_prices(monkeypatch):
    monkeypatch.setattr(runtime, "get_price_provider", lambda: FakePrices())


def test_run_backtest_ok(fake_prices):
    out = run_backtest("2024-04-01", "2024-05-31", cash=10_000.0, max_positions=2)
    assert out["status"] == "ok"
    assert set(out["metrics"]) == {"total_return", "max_drawdown", "sharpe",
                                   "win_rate", "num_fills"}
    assert out["num_days"] > 0 and out["final_equity"] > 0


def test_run_backtest_bad_dates():
    out = run_backtest("nope", "2024-05-31")
    assert out["status"] == "error" and "invalid date" in out["error"]


def test_run_backtest_empty_range(fake_prices):
    out = run_backtest("2030-01-01", "2030-01-05")
    assert out["status"] == "error"


def test_run_backtest_invalid_config_not_raised(fake_prices):
    out = run_backtest("2024-05-31", "2024-04-01")  # start > end:返回 error,不抛
    assert out["status"] == "error"
