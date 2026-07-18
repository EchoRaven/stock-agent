import datetime as dt

from app.data.base import PriceProvider
from app.screener.base import Screener
from app.services.analysis_service import default_screener, run_screen, run_screen_on_bars
from tests.helpers import make_bars


class FakeProvider(PriceProvider):
    def __init__(self):
        self.requests = []

    def get_daily_bars(self, symbol, start, end):
        self.requests.append((symbol, start, end))
        base = 100.0 if symbol == "GOOD" else 500.0
        step = 1.0 if symbol == "GOOD" else -1.0
        return make_bars(start="2024-01-01", days=120, base=base, step=step)


def test_default_screener_composition():
    s = default_screener()
    assert isinstance(s, Screener)


def test_run_screen_ranks_uptrend_first():
    provider = FakeProvider()
    as_of = dt.date(2024, 6, 28)
    scores = run_screen(provider, ["BAD", "GOOD"], top_n=2, lookback_days=400, as_of=as_of)
    assert [s.symbol for s in scores] == ["GOOD", "BAD"]
    assert scores[0].total > scores[1].total
    # 请求区间正确:start = as_of - lookback
    sym, start, end = provider.requests[0]
    assert end == as_of
    assert start == as_of - dt.timedelta(days=400)


def test_run_screen_on_bars_ranks_pre_fetched_bars():
    """run_screen_on_bars 直接对已抓好的 bars 打分,不做任何抓取。"""
    bars = {
        "GOOD": make_bars(start="2024-01-01", days=120, base=100.0, step=1.0),
        "BAD": make_bars(start="2024-01-01", days=120, base=500.0, step=-1.0),
    }
    scores = run_screen_on_bars(bars, top_n=2)
    assert [s.symbol for s in scores] == ["GOOD", "BAD"]
    assert scores[0].total > scores[1].total


def test_run_screen_delegates_to_run_screen_on_bars(monkeypatch):
    """run_screen 现在是"抓取(市场数据服务)+ 委托给 run_screen_on_bars"的薄封装。"""
    provider = FakeProvider()
    as_of = dt.date(2024, 6, 28)
    seen = {}
    from app.services import analysis_service

    original = analysis_service.run_screen_on_bars

    def spy(bars_by_symbol, top_n):
        seen["bars"] = bars_by_symbol
        seen["top_n"] = top_n
        return original(bars_by_symbol, top_n)

    monkeypatch.setattr(analysis_service, "run_screen_on_bars", spy)
    scores = run_screen(provider, ["GOOD", "BAD"], top_n=2, lookback_days=400, as_of=as_of)
    assert seen["top_n"] == 2
    assert set(seen["bars"].keys()) == {"GOOD", "BAD"}
    assert [s.symbol for s in scores] == ["GOOD", "BAD"]
