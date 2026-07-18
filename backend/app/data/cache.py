import datetime as dt
from pathlib import Path

import pandas as pd

from app.data.base import PriceProvider


class CachedPriceProvider(PriceProvider):
    """parquet 本地缓存;命中范围直接切片,否则回源并合并写回。"""

    def __init__(self, inner: PriceProvider, cache_dir: Path):
        self._inner = inner
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def get_daily_bars(self, symbol: str, start: dt.date, end: dt.date):
        cached = self._load(symbol)
        if cached is not None and self._covers(cached, start, end):
            return self._slice(cached, start, end)
        fetched = self._inner.get_daily_bars(symbol, start, end)
        merged = self._merge(cached, fetched)
        if not merged.empty:
            merged.to_parquet(self._path(symbol))
        return self._slice(merged, start, end)

    def _path(self, symbol: str) -> Path:
        return self._dir / f"{symbol.upper()}.parquet"

    def _load(self, symbol: str):
        path = self._path(symbol)
        if not path.exists():
            return None
        return pd.read_parquet(path)

    @staticmethod
    def _covers(df: pd.DataFrame, start: dt.date, end: dt.date) -> bool:
        if df.empty:
            return False
        return df.index.min().date() <= start and df.index.max().date() >= end

    @staticmethod
    def _merge(cached, fetched) -> pd.DataFrame:
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
