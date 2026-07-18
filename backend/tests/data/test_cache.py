import datetime as dt

from app.data.base import PriceProvider, empty_bars
from app.data.cache import CachedPriceProvider
from tests.helpers import make_bars


class CountingProvider(PriceProvider):
    def __init__(self, bars):
        self.bars = bars
        self.calls = 0
        self.requests = []  # [(start, end), ...] 记录每次内层调用的实际请求区间

    def get_daily_bars(self, symbol, start, end):
        self.calls += 1
        self.requests.append((start, end))
        mask = (self.bars.index.date >= start) & (self.bars.index.date <= end)
        return self.bars.loc[mask]


def test_second_identical_call_hits_cache(tmp_path):
    inner = CountingProvider(make_bars(start="2024-01-01", days=10))  # 至 2024-01-12(周五)
    p = CachedPriceProvider(inner, tmp_path)
    start, end = dt.date(2024, 1, 1), dt.date(2024, 1, 12)
    first = p.get_daily_bars("AAA", start, end)
    second = p.get_daily_bars("AAA", start, end)
    assert inner.calls == 1
    assert first.equals(second)
    assert len(first) == 10


def test_uncovered_range_refetches_and_merges(tmp_path):
    inner = CountingProvider(make_bars(start="2024-01-01", days=20))  # 至 2024-01-26
    p = CachedPriceProvider(inner, tmp_path)
    p.get_daily_bars("AAA", dt.date(2024, 1, 1), dt.date(2024, 1, 12))
    assert inner.calls == 1
    out = p.get_daily_bars("AAA", dt.date(2024, 1, 1), dt.date(2024, 1, 26))
    assert inner.calls == 2
    assert len(out) == 20
    # 合并后再次请求子区间应命中缓存
    p.get_daily_bars("AAA", dt.date(2024, 1, 8), dt.date(2024, 1, 19))
    assert inner.calls == 2


def test_subrange_served_from_cache(tmp_path):
    inner = CountingProvider(make_bars(start="2024-01-01", days=10))
    p = CachedPriceProvider(inner, tmp_path)
    p.get_daily_bars("AAA", dt.date(2024, 1, 1), dt.date(2024, 1, 12))
    sub = p.get_daily_bars("AAA", dt.date(2024, 1, 3), dt.date(2024, 1, 10))
    assert inner.calls == 1
    assert sub.index.min().date() >= dt.date(2024, 1, 3)
    assert sub.index.max().date() <= dt.date(2024, 1, 10)


def test_disjoint_ranges_do_not_fake_coverage(tmp_path):
    """fetch-union-and-replace: 第二次(不连续的)调用不会只抓自己的小区间,
    而是把"已记录区间 ∪ 本次请求区间"整体重新从数据源抓一次并整体替换缓存文件,
    因此缓存里任何时候只存在一段连续、单一复权基准的数据。凡命中必是单次完整
    抓取的连续区间,不存在缺口——所以覆盖该并集的后续查询是合法的缓存命中。"""
    inner = CountingProvider(make_bars(start="2024-01-01", days=60))  # 至 2024-03-22
    p = CachedPriceProvider(inner, tmp_path)
    p.get_daily_bars("AAA", dt.date(2024, 1, 1), dt.date(2024, 1, 12))
    p.get_daily_bars("AAA", dt.date(2024, 3, 11), dt.date(2024, 3, 15))
    assert inner.calls == 2  # 第二次调用一次性抓取并集区间 [01-01, 03-15]
    full = p.get_daily_bars("AAA", dt.date(2024, 1, 1), dt.date(2024, 3, 15))
    assert inner.calls == 2  # 并集区间内的连续缓存,合法命中,无需回源
    expected = inner.get_daily_bars("AAA", dt.date(2024, 1, 1), dt.date(2024, 3, 15))
    assert len(full) == len(expected)


def test_union_fetch_requests_full_span(tmp_path):
    """未命中时抓取的是"已记录区间 ∪ 本次请求区间"的整个并集,而不是只抓
    本次请求的(可能与已有数据不连续的)小区间——这是避免复权基准断层拼接的关键。"""
    inner = CountingProvider(make_bars(start="2024-01-01", days=60))  # 至 2024-03-22
    p = CachedPriceProvider(inner, tmp_path)
    p.get_daily_bars("AAA", dt.date(2024, 1, 1), dt.date(2024, 1, 12))
    p.get_daily_bars("AAA", dt.date(2024, 3, 11), dt.date(2024, 3, 15))
    assert inner.requests[1] == (dt.date(2024, 1, 1), dt.date(2024, 3, 15))

    full = p.get_daily_bars("AAA", dt.date(2024, 1, 1), dt.date(2024, 3, 15))
    expected = inner.get_daily_bars("AAA", dt.date(2024, 1, 1), dt.date(2024, 3, 15))
    assert len(full) == len(expected)  # 并集区间内部无缺口


class OutageProvider(PriceProvider):
    def __init__(self, bars):
        self.bars = bars
        self.calls = 0
        self.outage = False

    def get_daily_bars(self, symbol, start, end):
        self.calls += 1
        if self.outage:
            return empty_bars()
        mask = (self.bars.index.date >= start) & (self.bars.index.date <= end)
        return self.bars.loc[mask]


def test_failed_fetch_does_not_poison_coverage(tmp_path):
    inner = OutageProvider(make_bars(start="2024-01-01", days=100))
    p = CachedPriceProvider(inner, tmp_path)
    p.get_daily_bars("AAA", dt.date(2024, 1, 1), dt.date(2024, 1, 31))
    inner.outage = True
    partial = p.get_daily_bars("AAA", dt.date(2024, 1, 1), dt.date(2024, 5, 17))
    assert inner.calls == 2
    inner.outage = False
    full = p.get_daily_bars("AAA", dt.date(2024, 1, 1), dt.date(2024, 5, 17))
    assert inner.calls == 3  # 断网期间的空抓取不得记为已覆盖
    assert len(full) > len(partial)


def test_today_not_recorded_as_covered(tmp_path):
    today = dt.date.today()
    start = today - dt.timedelta(days=30)
    inner = CountingProvider(make_bars(start=start.isoformat(), days=15))
    p = CachedPriceProvider(inner, tmp_path)
    p.get_daily_bars("AAA", start, today)
    assert inner.calls == 1
    p.get_daily_bars("AAA", start, today)
    assert inner.calls == 2  # end=今天 不记覆盖,当日重复查询必须回源
    p.get_daily_bars("AAA", start, today - dt.timedelta(days=1))
    assert inner.calls == 2  # 截止昨天的子区间仍命中缓存
