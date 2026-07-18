import datetime as dt
import math

from app.data.base import PriceProvider
from app.data.fundamentals_edgar import FundamentalsProvider
from app.data.news_finnhub import NewsProvider
from app.data.sanitize import sanitize_text, wrap_untrusted
from app.screener.indicators import pct_return, rsi, sma
from app.services.market_data_service import fetch_bars


def _num(value):
    """float 化;NaN/不可转换 → None;保留 4 位小数。"""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else round(f, 4)


def summarize_bars(bars) -> dict:
    if bars is None or bars.empty:
        return {"num_bars": 0}
    close = bars["close"]
    n = len(close)
    return {
        "num_bars": int(n),
        "last_date": bars.index[-1].date().isoformat(),
        "last_close": _num(close.iloc[-1]),
        "chg_5d": _num(pct_return(close, 5).iloc[-1]) if n > 5 else None,
        "chg_20d": _num(pct_return(close, 20).iloc[-1]) if n > 20 else None,
        "sma20": _num(sma(close, 20).iloc[-1]) if n >= 20 else None,
        "sma50": _num(sma(close, 50).iloc[-1]) if n >= 50 else None,
        "rsi14": _num(rsi(close, 14).iloc[-1]) if n > 14 else None,
        "avg_vol_20": _num(bars["volume"].iloc[-20:].mean()),
    }


def _cleaned_news(items) -> tuple:
    """清洗每条新闻,并把整块渲染成定界包裹的不可信材料块。"""
    cleaned = [
        {
            "date": item.published_at.isoformat(),
            "source": sanitize_text(item.source, 60),
            "headline": sanitize_text(item.headline, 200),
            "summary": sanitize_text(item.summary, 500),
        }
        for item in items
    ]
    body = "\n".join(f"- [{n['date']}] ({n['source']}) {n['headline']} — {n['summary']}"
                     for n in cleaned)
    return cleaned, wrap_untrusted(body or "(区间内无新闻)")


def _points(points) -> list:
    return [{"end": p.end.isoformat(), "value": p.value, "fiscal": p.fiscal} for p in points]


def get_stock_briefing(
    symbol: str,
    price_provider: PriceProvider,
    news_provider: NewsProvider,
    fundamentals_provider: FundamentalsProvider,
    as_of: dt.date,
    lookback_days: int = 250,
    news_days: int = 7,
) -> dict:
    """组装单只标的的结构化材料包(供 LLM 委员会分析)。JSON 可序列化。"""
    sym = symbol.strip().upper()
    start = as_of - dt.timedelta(days=lookback_days)
    bars_map, _skipped = fetch_bars(price_provider, [sym], start, as_of)
    news_items = news_provider.get_company_news(sym, as_of - dt.timedelta(days=news_days), as_of)
    funds = fundamentals_provider.get_fundamentals(sym)
    news, news_block = _cleaned_news(news_items)
    return {
        "symbol": sym,
        "as_of": as_of.isoformat(),
        "bars": summarize_bars(bars_map.get(sym)),
        "news": news,
        "news_block": news_block,
        "fundamentals": {
            "revenue": _points(funds.revenue),
            "net_income": _points(funds.net_income),
            "eps": _points(funds.eps),
        },
    }
