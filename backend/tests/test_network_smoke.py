import datetime as dt

import pytest

from app.data.prices_yfinance import YFinancePriceProvider


@pytest.mark.network
def test_yfinance_real_fetch():
    """联网冒烟:默认跳过,pytest -m network 手动运行。"""
    end = dt.date.today()
    start = end - dt.timedelta(days=30)
    df = YFinancePriceProvider().get_daily_bars("AAPL", start, end)
    assert not df.empty
    assert {"open", "close"}.issubset(df.columns)
