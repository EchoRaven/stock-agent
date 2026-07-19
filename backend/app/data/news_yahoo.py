import datetime as dt
import logging

import yfinance as yf

from app.data.news_finnhub import NewsItem, NewsProvider

logger = logging.getLogger(__name__)


class YahooNewsProvider(NewsProvider):
    """免 key 的 Yahoo(yfinance `.news`)公司新闻源。仅返回近期新闻,客户端按区间过滤。"""

    def __init__(self, timeout: float = 10.0, max_items: int = 20):
        self._timeout = timeout
        self._max_items = max_items

    def get_company_news(self, symbol: str, start: dt.date, end: dt.date) -> list:
        sym = symbol.strip().upper()
        try:
            raw = yf.Ticker(sym).news or []
        except Exception as exc:  # yfinance 抛出多种未文档化异常,统一兜底不崩
            logger.warning("yahoo 新闻抓取失败(%s),返回空列表", exc)
            return []

        items = []
        for entry in raw:
            try:
                c = entry.get("content") if isinstance(entry, dict) else None
                if not isinstance(c, dict):
                    continue

                title = str(c.get("title") or "").strip()
                if not title:
                    continue

                pub = c.get("pubDate")
                try:
                    published_at = dt.datetime.fromisoformat(str(pub).replace("Z", "+00:00")).date()
                except (ValueError, TypeError):
                    continue

                summary = str(c.get("summary") or "")

                provider = c.get("provider")
                if provider is None:
                    provider = {}
                elif not isinstance(provider, dict):
                    continue  # 畸形 provider(非 dict),跳过该条而非崩溃
                source = str(provider.get("displayName") or "Yahoo Finance")

                cu = c.get("canonicalUrl") or c.get("clickThroughUrl")
                if cu is None:
                    cu = {}
                elif not isinstance(cu, dict):
                    continue  # 畸形 url 字段(非 dict),跳过该条而非崩溃
                url = str(cu.get("url") or "")

                if start <= published_at <= end:
                    items.append(NewsItem(published_at, title, summary, source, url))
            except Exception as exc:  # 单条解析异常绝不能拖垮整批,跳过该条继续
                logger.warning("yahoo 单条新闻解析失败(%s),跳过该条", exc)
                continue

        items.sort(key=lambda n: n.published_at, reverse=True)
        return items[: self._max_items]
