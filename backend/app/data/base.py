import datetime as dt
from abc import ABC, abstractmethod

import pandas as pd

BAR_COLUMNS = ["open", "high", "low", "close", "volume"]


def empty_bars() -> pd.DataFrame:
    return pd.DataFrame(columns=BAR_COLUMNS, index=pd.DatetimeIndex([]))


class PriceProvider(ABC):
    """日线行情来源抽象。实盘与回放实现同一接口,上层无感知。"""

    @abstractmethod
    def get_daily_bars(self, symbol: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        """返回 [start, end] 闭区间日线:升序 DatetimeIndex(无时区),
        列为 open/high/low/close/volume。无数据时返回 empty_bars()。
        返回的 DataFrame 归调用方所有,实现方必须返回副本。"""
