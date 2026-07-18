import datetime as dt

from app.data.base import PriceProvider, empty_bars


class ReplayPriceProvider(PriceProvider):
    """回测数据源:只暴露 as_of 及以前的数据,杜绝未来函数。"""

    def __init__(self, bars_by_symbol: dict):
        self._bars = bars_by_symbol
        self._as_of = None

    def set_as_of(self, as_of: dt.date) -> None:
        self._as_of = as_of

    def get_daily_bars(self, symbol: str, start: dt.date, end: dt.date):
        if self._as_of is None:
            raise RuntimeError("set_as_of() must be called before reading data")
        end = min(end, self._as_of)
        df = self._bars.get(symbol)
        if df is None or df.empty:
            return empty_bars()
        mask = (df.index.date >= start) & (df.index.date <= end)
        return df.loc[mask]
