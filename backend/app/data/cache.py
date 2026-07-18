import datetime as dt
import json
from pathlib import Path

import pandas as pd

from app.data.base import PriceProvider


class CachedPriceProvider(PriceProvider):
    """parquet 本地缓存 + 已抓取区间元数据(.intervals.json)。

    fetch-union-and-replace: 未命中时不是只抓本次请求的小区间再与旧数据拼接,
    而是把"已记录区间 ∪ 本次请求区间"整体从数据源重新抓一次,并用这次抓到的
    frame 整体替换 parquet 文件与区间记录(不与旧行合并)。这样文件里任何时候
    只存在一段连续、单一复权基准(auto_adjust)的数据,不会出现"两次相隔数月的
    抓取被拼接成一份文件、复权基准在拼接处发生断层"的问题。
    """

    def __init__(self, inner: PriceProvider, cache_dir: Path):
        self._inner = inner
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def get_daily_bars(self, symbol: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        cached = self._load(symbol)
        if cached is not None and self._covers(symbol, start, end):
            return self._slice(cached, start, end)

        span_start, span_end = self._union_span(symbol, start, end)
        fetched = self._inner.get_daily_bars(symbol, span_start, span_end)
        if fetched.empty:
            # 抓取失败/为空:优雅降级,原样返回旧缓存能覆盖的部分,不记录覆盖
            if cached is not None:
                return self._slice(cached, start, end)
            return self._slice(fetched, start, end)

        fetched.to_parquet(self._path(symbol))
        self._replace_interval(symbol, span_start, span_end)
        return self._slice(fetched, start, end)

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

    def _union_span(self, symbol: str, start: dt.date, end: dt.date) -> tuple:
        """已记录区间(至多一个,见 _replace_interval)与本次请求区间的并集端点。"""
        starts = [start]
        ends = [end]
        for a, b in self._intervals(symbol):
            starts.append(dt.date.fromisoformat(a))
            ends.append(dt.date.fromisoformat(b))
        return min(starts), max(ends)

    def _replace_interval(self, symbol: str, start: dt.date, end: dt.date) -> None:
        # 当日抓到的可能是盘中的半根K线,不把今天记为已覆盖,当日重复查询总是回源
        end = min(end, dt.date.today() - dt.timedelta(days=1))
        if end < start:
            return
        self._meta_path(symbol).write_text(json.dumps([[start.isoformat(), end.isoformat()]]))

    def _covers(self, symbol: str, start: dt.date, end: dt.date) -> bool:
        for a, b in self._intervals(symbol):
            if dt.date.fromisoformat(a) <= start and dt.date.fromisoformat(b) >= end:
                return True
        return False

    @staticmethod
    def _slice(df: pd.DataFrame, start: dt.date, end: dt.date) -> pd.DataFrame:
        if df.empty:
            return df.copy()
        mask = (df.index.date >= start) & (df.index.date <= end)
        return df.loc[mask].copy()
