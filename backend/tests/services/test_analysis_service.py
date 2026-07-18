import datetime as dt

from app.data.base import PriceProvider
from app.screener.base import Screener
from app.services.analysis_service import default_screener, run_screen
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
