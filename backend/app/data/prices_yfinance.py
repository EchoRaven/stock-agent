import datetime as dt

import pandas as pd
import yfinance as yf

from app.data.base import BAR_COLUMNS, PriceProvider, empty_bars


class YFinancePriceProvider(PriceProvider):
    """yfinance 日线(auto_adjust 复权)。列名与时区归一化到 BAR_COLUMNS 约定。"""

    def get_daily_bars(self, symbol: str, start: dt.date, end: dt.date):
        raw = yf.download(
            symbol,
            start=start.isoformat(),
            end=(end + dt.timedelta(days=1)).isoformat(),  # yfinance end 为开区间
            interval="1d",
            auto_adjust=True,
            progress=False,
        )
        if raw is None or raw.empty:
            return empty_bars()
        df = raw.copy()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [str(c[0]).lower() for c in df.columns]
        else:
            df.columns = [str(c).lower() for c in df.columns]
        df = df[BAR_COLUMNS]
        idx = pd.to_datetime(df.index)
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        df.index = idx.normalize()
        return df.sort_index()
