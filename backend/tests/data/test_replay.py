import datetime as dt

import pytest

from app.data.base import BAR_COLUMNS, empty_bars
from app.data.replay import ReplayPriceProvider
from tests.helpers import make_bars


def test_empty_bars_shape():
    df = empty_bars()
    assert list(df.columns) == BAR_COLUMNS
    assert df.empty


def test_replay_never_returns_future_rows():
    bars = make_bars(start="2024-01-01", days=10)  # 2024-01-01 ~ 2024-01-12 工作日
    p = ReplayPriceProvider({"AAA": bars})
    p.set_as_of(dt.date(2024, 1, 5))
    out = p.get_daily_bars("AAA", dt.date(2024, 1, 1), dt.date(2024, 1, 12))
    assert out.index.max().date() <= dt.date(2024, 1, 5)
    assert len(out) == 5  # 1/1 ~ 1/5 共 5 个工作日


def test_replay_requires_as_of():
    p = ReplayPriceProvider({"AAA": make_bars()})
    with pytest.raises(RuntimeError):
        p.get_daily_bars("AAA", dt.date(2024, 1, 1), dt.date(2024, 1, 5))


def test_replay_unknown_symbol_returns_empty():
    p = ReplayPriceProvider({})
    p.set_as_of(dt.date(2024, 1, 5))
    assert p.get_daily_bars("NOPE", dt.date(2024, 1, 1), dt.date(2024, 1, 5)).empty


def test_get_daily_bars_returns_defensive_copy():
    """调用方拿到的 DataFrame 归自己所有,修改它不得影响内部存储和后续取数。"""
    bars = make_bars(start="2024-01-01", days=10)
    p = ReplayPriceProvider({"AAA": bars})
    p.set_as_of(dt.date(2024, 1, 5))
    first = p.get_daily_bars("AAA", dt.date(2024, 1, 1), dt.date(2024, 1, 5))
    first.iloc[0, first.columns.get_loc("close")] = -999.0

    second = p.get_daily_bars("AAA", dt.date(2024, 1, 1), dt.date(2024, 1, 5))
    assert second.iloc[0]["close"] != -999.0
