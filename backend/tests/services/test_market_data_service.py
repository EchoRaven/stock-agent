import datetime as dt

from app.data.base import PriceProvider, empty_bars
from app.services.market_data_service import fetch_bars
from tests.helpers import make_bars


class MixedProvider(PriceProvider):
    """一个抓取失败、一个返回空、一个正常,验证逐标的隔离不互相影响。"""

    def get_daily_bars(self, symbol, start, end):
        if symbol == "BAD":
            raise RuntimeError("network down")
        if symbol == "EMPTY":
            return empty_bars()
        return make_bars(start="2024-01-01", days=10)


def test_fetch_bars_isolates_failures_and_empties():
    bars, skipped = fetch_bars(
        MixedProvider(), ["GOOD", "BAD", "EMPTY"], dt.date(2024, 1, 1), dt.date(2024, 1, 12)
    )
    assert list(bars.keys()) == ["GOOD"]
    assert len(bars["GOOD"]) == 10
    assert ("BAD", "network down") in skipped
    assert ("EMPTY", "empty") in skipped
    assert len(skipped) == 2


def test_fetch_bars_all_good_has_no_skips():
    class AlwaysGood(PriceProvider):
        def get_daily_bars(self, symbol, start, end):
            return make_bars(start="2024-01-01", days=5)

    bars, skipped = fetch_bars(AlwaysGood(), ["AAA", "BBB"], dt.date(2024, 1, 1), dt.date(2024, 1, 5))
    assert set(bars.keys()) == {"AAA", "BBB"}
    assert skipped == []
