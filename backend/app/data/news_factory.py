from app.data.news_finnhub import FinnhubNewsProvider, NewsProvider
from app.data.news_yahoo import YahooNewsProvider


def build_news_provider(settings) -> NewsProvider:
    """有 Finnhub key → Finnhub;否则 → 免key的 Yahoo(yfinance .news)。"""
    if settings.finnhub_api_key:
        return FinnhubNewsProvider(settings.finnhub_api_key)
    return YahooNewsProvider()
