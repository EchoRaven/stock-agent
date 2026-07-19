import datetime as dt

from app.screener.sp500_pit import constituents_asof


def test_constituents_asof_picks_most_recent_on_or_before(tmp_path):
    csv_path = tmp_path / "pit.csv"
    csv_path.write_text(
        "date,tickers\n"
        '2020-01-01,"AAA,BBB,CCC"\n'
        '2020-06-01,"AAA,BBB,DDD"\n'
    )
    # Between the two dates -> earlier set.
    assert constituents_asof(dt.date(2020, 3, 1), path=csv_path) == ["AAA", "BBB", "CCC"]
    # After both dates -> later set.
    assert constituents_asof(dt.date(2020, 12, 31), path=csv_path) == ["AAA", "BBB", "DDD"]
    # Exactly on a constituent date -> that date's set.
    assert constituents_asof(dt.date(2020, 6, 1), path=csv_path) == ["AAA", "BBB", "DDD"]


def test_asof_before_all_returns_empty(tmp_path):
    csv_path = tmp_path / "pit.csv"
    csv_path.write_text(
        "date,tickers\n"
        '2020-01-01,"AAA,BBB"\n'
    )
    assert constituents_asof(dt.date(2019, 1, 1), path=csv_path) == []


def test_dedup_and_uppercase(tmp_path):
    csv_path = tmp_path / "pit.csv"
    csv_path.write_text(
        "date,tickers\n"
        '2020-01-01,"aaa,BBB,aaa,ccc"\n'
    )
    assert constituents_asof(dt.date(2020, 1, 1), path=csv_path) == ["AAA", "BBB", "CCC"]
