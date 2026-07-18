import datetime as dt
import json
from pathlib import Path

import pandas as pd

from app.data.base import PriceProvider


def _coalesce(intervals: list) -> list:
    """按起点排序后合并重叠或相邻(间隔≤1天)的日期区间。"""
    if not intervals:
        return []
    items = sorted((dt.date.fromisoformat(a), dt.date.fromisoformat(b)) for a, b in intervals)
    merged = [items[0]]
    for start, end in items[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + dt.timedelta(days=1):
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return [[s.isoformat(), e.isoformat()] for s, e in merged]


class CachedPriceProvider(PriceProvider):
    """parquet 本地缓存 + 已抓取区间元数据(.intervals.json)。

    只有请求范围完整落在单个已抓取区间内才算命中,防止由多次不连续
    抓取拼成的缓存被 min/max 误判为完整覆盖。
    """

    def __init__(self, inner: PriceProvider, cache_dir: Path):
        self._inner = inner
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def get_daily_bars(self, symbol: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        cached = self._load(symbol)
        if cached is not None and self._covers(symbol, start, end):
            return self._slice(cached, start, end)
        fetched = self._inner.get_daily_bars(symbol, start, end)
        merged = self._merge(cached, fetched)
        if not fetched.empty:
            merged.to_parquet(self._path(symbol))
            self._record_interval(symbol, start, end)
        return self._slice(merged, start, end)

    def _path(self, symbol: str) -> Path:
        return self._dir / f"{symbol.upper()}.parquet"

    def _meta_path(self, symbol: str) -> Path:
        return self._dir / f"{symbol.upper()}.intervals.json"

    def _load(self, symbol: str) -> pd.DataFrame | None:
        path = self._path(symbol)
        if not path.exists():
            return None
        return pd.read_parquet(path)

    def _intervals(self, symbol: str) -> list:
        path = self._meta_path(symbol)
        if not path.exists():
            return []
        return json.loads(path.read_text())

    def _record_interval(self, symbol: str, start: dt.date, end: dt.date) -> None:
        # 当日抓到的可能是盘中的半根K线,不把今天记为已覆盖,当日重复查询总是回源
        end = min(end, dt.date.today() - dt.timedelta(days=1))
        if end < start:
            return
        intervals = self._intervals(symbol)
        intervals.append([start.isoformat(), end.isoformat()])
        self._meta_path(symbol).write_text(json.dumps(_coalesce(intervals)))

    def _covers(self, symbol: str, start: dt.date, end: dt.date) -> bool:
        for a, b in self._intervals(symbol):
            if dt.date.fromisoformat(a) <= start and dt.date.fromisoformat(b) >= end:
                return True
        return False

    @staticmethod
    def _merge(cached: pd.DataFrame | None, fetched: pd.DataFrame) -> pd.DataFrame:
        if cached is None or cached.empty:
            return fetched
        merged = pd.concat([cached, fetched])
        merged = merged[~merged.index.duplicated(keep="last")]
        return merged.sort_index()

    @staticmethod
    def _slice(df: pd.DataFrame, start: dt.date, end: dt.date) -> pd.DataFrame:
        if df.empty:
            return df
        mask = (df.index.date >= start) & (df.index.date <= end)
        return df.loc[mask]
