import datetime as dt

from app.util.trading_day import ET_ZONE, et_trading_day


def test_summer_evening_utc_is_previous_et_day():
    # 美东夏令时 UTC-4:UTC 7/18 02:00 = ET 7/17 22:00
    assert et_trading_day(dt.datetime(2026, 7, 18, 2, 0, tzinfo=dt.UTC)) == dt.date(2026, 7, 17)


def test_winter_early_utc_is_previous_et_day():
    # 美东标准时 UTC-5:UTC 1/15 04:30 = ET 1/14 23:30
    assert et_trading_day(dt.datetime(2026, 1, 15, 4, 30, tzinfo=dt.UTC)) == dt.date(2026, 1, 14)


def test_afternoon_utc_same_day():
    assert et_trading_day(dt.datetime(2026, 7, 17, 18, 0, tzinfo=dt.UTC)) == dt.date(2026, 7, 17)


def test_naive_datetime_treated_as_utc():
    assert et_trading_day(dt.datetime(2026, 7, 18, 2, 0)) == dt.date(2026, 7, 17)
    assert ET_ZONE.key == "America/New_York"
