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


def latest_closes(bars_by_symbol: dict) -> dict:
    """每标的最后一根日线的收盘价;空 DataFrame 跳过。"""
    out = {}
    for symbol, bars in bars_by_symbol.items():
        if bars is not None and not bars.empty:
            out[symbol] = float(bars["close"].iloc[-1])
    return out


def latest_closes_for(provider: PriceProvider, symbols: list, as_of: dt.date,
                      lookback_days: int = 14) -> dict:
    """服务端取闸门参考价(最新收盘)。调用方 payload 没有价格通道。"""
    bars, _skipped = fetch_bars(provider, symbols,
                                as_of - dt.timedelta(days=lookback_days), as_of)
    return latest_closes(bars)


def open_prices_for(provider: PriceProvider, symbols: list, on_date: dt.date) -> dict:
    """撮合日开盘价:取 on_date 当日 bar 的 open;当日无 bar 的标的缺席(由 broker 撤单留痕)。"""
    bars, _skipped = fetch_bars(provider, symbols, on_date, on_date)
    return {symbol: float(df["open"].iloc[-1]) for symbol, df in bars.items() if not df.empty}
