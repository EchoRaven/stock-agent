import datetime as dt

from app.data.base import PriceProvider


def fetch_bars(
    provider: PriceProvider, symbols: list, start: dt.date, end: dt.date
) -> tuple:
    """逐标的抓取行情,单只失败不影响其余。

    返回 (bars_by_symbol, skipped):
    - bars_by_symbol: 抓取成功且非空的 {symbol: DataFrame}
    - skipped: [(symbol, reason), ...],reason 为异常信息或 "empty"
    """
    bars = {}
    skipped = []
    for symbol in symbols:
        try:
            df = provider.get_daily_bars(symbol, start, end)
        except Exception as exc:
            skipped.append((symbol, str(exc) or "empty"))
            continue
        if df.empty:
            skipped.append((symbol, "empty"))
            continue
        bars[symbol] = df
    return bars, skipped
