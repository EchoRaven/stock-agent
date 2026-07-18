import datetime as dt
import json

import pytest

import app.mcp.runtime as runtime
from app.data.base import PriceProvider
from app.mcp.tool_screener import run_screener
from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.signal_repo import get_signals
from tests.helpers import make_bars


class FakePrices(PriceProvider):
    """只有 AAPL 是上升趋势,其余全是下跌趋势。"""

    def get_daily_bars(self, symbol, start, end):
        if symbol == "AAPL":
            return make_bars(start="2024-01-01", days=120, base=100.0, step=1.0)
        return make_bars(start="2024-01-01", days=120, base=500.0, step=-1.0)


@pytest.fixture
def factory(monkeypatch):
    engine = make_engine(":memory:")
    init_db(engine)
    factory = make_session_factory(engine)
    monkeypatch.setattr(runtime, "get_price_provider", lambda: FakePrices())
    monkeypatch.setattr(runtime, "open_session", lambda: factory())
    return factory


def test_run_screener_returns_ranked_and_persists(factory):
    out = run_screener(top_n=3)
    assert out["as_of"] == dt.date.today().isoformat()
    assert len(out["results"]) == 3
    assert out["results"][0]["rank"] == 1
    assert out["results"][0]["symbol"] == "AAPL"  # 唯一上升趋势的票排第一
    assert set(out["results"][0]["parts"]) == {"trend", "momentum", "volume"}
    with factory() as session:
        rows = get_signals(session, dt.date.today())
    assert len(rows) == 3 and rows[0].symbol == "AAPL"


def test_output_json_serializable(factory):
    json.dumps(run_screener(top_n=2))


def test_top_n_below_one_returns_error(factory):
    out = run_screener(top_n=0)
    assert out == {"status": "error", "error": "top_n must be >= 1"}


def test_top_n_negative_returns_error(factory):
    out = run_screener(top_n=-3)
    assert out["status"] == "error"
