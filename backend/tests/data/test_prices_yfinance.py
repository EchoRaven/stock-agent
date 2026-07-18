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


def test_end_date_is_inclusive(monkeypatch):
    captured = {}

    def fake_download(symbol, **kwargs):
        captured.update(kwargs, symbol=symbol)
        return pd.DataFrame()

    monkeypatch.setattr(mod.yf, "download", fake_download)
    mod.YFinancePriceProvider().get_daily_bars("AAPL", dt.date(2024, 1, 2), dt.date(2024, 1, 4))
    assert captured["end"] == "2024-01-05"  # yfinance end 开区间,+1 天保证闭区间语义
