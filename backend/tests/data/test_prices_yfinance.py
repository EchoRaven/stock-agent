import datetime as dt

import numpy as np
import pandas as pd

import app.data.prices_yfinance as mod
from app.data.base import BAR_COLUMNS


def _fake_raw(multiindex: bool) -> pd.DataFrame:
    idx = pd.date_range("2024-01-02", periods=3, tz="America/New_York")
    data = {c: np.arange(3, dtype=float) + i for i, c in enumerate(["Open", "High", "Low", "Close", "Volume"])}
    df = pd.DataFrame(data, index=idx)
    if multiindex:
        df.columns = pd.MultiIndex.from_product([["Open", "High", "Low", "Close", "Volume"], ["AAPL"]])
    return df


def test_normalizes_plain_columns(monkeypatch):
    monkeypatch.setattr(mod.yf, "download", lambda *a, **k: _fake_raw(False))
    df = mod.YFinancePriceProvider().get_daily_bars("AAPL", dt.date(2024, 1, 2), dt.date(2024, 1, 4))
    assert list(df.columns) == BAR_COLUMNS
    assert df.index.tz is None
    assert len(df) == 3


def test_normalizes_multiindex_columns(monkeypatch):
    monkeypatch.setattr(mod.yf, "download", lambda *a, **k: _fake_raw(True))
    df = mod.YFinancePriceProvider().get_daily_bars("AAPL", dt.date(2024, 1, 2), dt.date(2024, 1, 4))
    assert list(df.columns) == BAR_COLUMNS


def test_empty_download_returns_empty_bars(monkeypatch):
    monkeypatch.setattr(mod.yf, "download", lambda *a, **k: pd.DataFrame())
    df = mod.YFinancePriceProvider().get_daily_bars("AAPL", dt.date(2024, 1, 2), dt.date(2024, 1, 4))
    assert df.empty and list(df.columns) == BAR_COLUMNS


def test_sorts_and_normalizes_index(monkeypatch):
    idx = pd.DatetimeIndex(["2024-01-03 09:30", "2024-01-02 09:30"], tz="America/New_York")
    raw = pd.DataFrame(
        {
            "Open": [30.0, 20.0],
            "High": [31.0, 21.0],
            "Low": [29.0, 19.0],
            "Close": [30.5, 20.5],
            "Volume": [300.0, 200.0],
        },
        index=idx,
    )
    monkeypatch.setattr(mod.yf, "download", lambda *a, **k: raw)
    df = mod.YFinancePriceProvider().get_daily_bars("AAPL", dt.date(2024, 1, 2), dt.date(2024, 1, 3))
    assert df.index.is_monotonic_increasing
    assert df.index.tz is None
    assert (df.index == df.index.normalize()).all()
    assert df["open"].tolist() == [20.0, 30.0]


def test_end_date_is_inclusive(monkeypatch):
    captured = {}

    def fake_download(symbol, **kwargs):
        captured.update(kwargs, symbol=symbol)
        return pd.DataFrame()

    monkeypatch.setattr(mod.yf, "download", fake_download)
    mod.YFinancePriceProvider().get_daily_bars("AAPL", dt.date(2024, 1, 2), dt.date(2024, 1, 4))
    assert captured["end"] == "2024-01-05"  # yfinance end 开区间,+1 天保证闭区间语义
