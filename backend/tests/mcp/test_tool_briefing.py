import json

import app.mcp.runtime as runtime
from app.data.base import PriceProvider
from app.data.fundamentals_edgar import FundamentalsProvider, FundamentalsSummary
from app.data.news_finnhub import NewsItem, NewsProvider
from app.data.sanitize import DELIM_OPEN
from app.mcp.tool_briefing import get_stock_briefing
from tests.helpers import make_bars


class FakePrices(PriceProvider):
    def get_daily_bars(self, symbol, start, end):
        return make_bars(start="2024-01-01", days=120)


class FakeNews(NewsProvider):
    def get_company_news(self, symbol, start, end):
        return [NewsItem(end, "headline", "summary", "src", "u")]


class FakeFunds(FundamentalsProvider):
    def get_fundamentals(self, symbol):
        return FundamentalsSummary(symbol)


def test_tool_briefing_delegates(monkeypatch):
    monkeypatch.setattr(runtime, "get_price_provider", lambda: FakePrices())
    monkeypatch.setattr(runtime, "get_news_provider", lambda: FakeNews())
    monkeypatch.setattr(runtime, "get_fundamentals_provider", lambda: FakeFunds())
    out = get_stock_briefing("aapl")
    assert out["symbol"] == "AAPL"
    assert out["bars"]["num_bars"] == 120
    assert DELIM_OPEN in out["news_block"]
    json.dumps(out)  # JSON 可序列化
