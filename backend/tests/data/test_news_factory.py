import types

from app.data.news_factory import build_news_provider
from app.data.news_finnhub import FinnhubNewsProvider
from app.data.news_yahoo import YahooNewsProvider


def test_uses_finnhub_when_key_present():
    settings = types.SimpleNamespace(finnhub_api_key="some-key")
    provider = build_news_provider(settings)
    assert isinstance(provider, FinnhubNewsProvider)


def test_uses_yahoo_when_key_empty():
    settings = types.SimpleNamespace(finnhub_api_key="")
    provider = build_news_provider(settings)
    assert isinstance(provider, YahooNewsProvider)
