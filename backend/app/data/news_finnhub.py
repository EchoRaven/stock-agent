import datetime as dt
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

COMPANY_NEWS_URL = "https://finnhub.io/api/v1/company-news"


@dataclass(frozen=True)
class NewsItem:
    published_at: dt.date
    headline: str
    summary: str
    source: str
    url: str


class NewsProvider(ABC):
    """公司新闻来源抽象。"""

    @abstractmethod
    def get_company_news(self, symbol: str, start: dt.date, end: dt.date) -> list:
        """返回 [start, end] 区间的公司新闻(NewsItem 列表,新→旧)。失败返回 []。"""


class FinnhubNewsProvider(NewsProvider):
    """Finnhub 免费档 company-news。无 API key 或请求失败:告警并返回 [],不崩。"""

    def __init__(self, api_key: str, timeout: float = 10.0, max_items: int = 20):
        self._api_key = api_key or ""
        self._timeout = timeout
        self._max_items = max_items

    def get_company_news(self, symbol: str, start: dt.date, end: dt.date) -> list:
        if not self._api_key:
            logger.warning("finnhub_api_key 未配置,跳过新闻抓取(返回空列表)")
            return []
        params = {"symbol": symbol.strip().upper(), "from": start.isoformat(),
                  "to": end.isoformat(), "token": self._api_key}
        try:
            resp = httpx.get(COMPANY_NEWS_URL, params=params, timeout=self._timeout)
            resp.raise_for_status()
            raw = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("finnhub 新闻抓取失败(HTTP %s),返回空列表", exc.response.status_code)
            return []
        except httpx.HTTPError as exc:
            logger.warning("finnhub 新闻抓取失败(%s),返回空列表", type(exc).__name__)
            return []
        items = []
        for entry in raw if isinstance(raw, list) else []:
            ts = entry.get("datetime")
            published = (dt.datetime.fromtimestamp(ts, tz=dt.UTC).date()
                         if isinstance(ts, (int, float)) else start)
            items.append(NewsItem(published, str(entry.get("headline", "")),
                                  str(entry.get("summary", "")), str(entry.get("source", "")),
                                  str(entry.get("url", ""))))
        items.sort(key=lambda n: n.published_at, reverse=True)
        return items[: self._max_items]
