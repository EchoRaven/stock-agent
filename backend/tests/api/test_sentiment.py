"""GET /api/sentiment —— 薄壳装配测试:注入 fake news provider + fake/无 gemini,全离线。
打分/清洗逻辑本身在 tests/services/test_news_sentiment_service.py 已覆盖,这里只测装配。
"""
import datetime as dt

from app.api.deps import get_gemini_client, get_news_provider
from app.data.news_finnhub import NewsItem
from app.main import app


class FakeNewsProvider:
    def __init__(self, items=None):
        self._items = items if items is not None else [
            NewsItem(published_at=dt.date(2026, 1, 4), headline="AAPL beats estimates",
                    summary="strong quarter", source="Reuters", url="https://example.com/1"),
        ]

    def get_company_news(self, symbol, start, end):
        return self._items


class FakeGemini:
    def generate_json(self, prompt):
        return {"sentiment": 0.5, "reason": "positive headline"}


def _override(provider=None, client=None):
    app.dependency_overrides[get_news_provider] = lambda: provider or FakeNewsProvider()
    app.dependency_overrides[get_gemini_client] = lambda: client


def _clear_overrides():
    app.dependency_overrides.pop(get_news_provider, None)
    app.dependency_overrides.pop(get_gemini_client, None)


def test_sentiment_returns_scored_dict_with_headlines(client):
    _override(client=FakeGemini())
    try:
        resp = client.get("/api/sentiment", params={"symbol": "AAPL"})
    finally:
        _clear_overrides()
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "AAPL"
    assert body["sentiment"] == 0.5
    assert body["scored"] is True
    assert body["news_count"] == 1
    assert len(body["headlines"]) == 1
    assert body["headlines"][0]["headline"] == "AAPL beats estimates"


def test_sentiment_without_gemini_client_is_unscored_but_not_empty(client):
    _override(client=None)
    try:
        resp = client.get("/api/sentiment", params={"symbol": "AAPL"})
    finally:
        _clear_overrides()
    assert resp.status_code == 200
    body = resp.json()
    assert body["sentiment"] is None
    assert body["scored"] is False
    assert len(body["headlines"]) == 1


def test_sentiment_respects_days_and_max_items_params(client):
    provider = FakeNewsProvider(items=[
        NewsItem(published_at=dt.date(2026, 1, i), headline=f"h{i}", summary="s",
                 source="src", url=f"https://example.com/{i}")
        for i in range(1, 4)
    ])
    _override(provider=provider, client=None)
    try:
        resp = client.get("/api/sentiment",
                          params={"symbol": "MSFT", "days": 3, "max_items": 2})
    finally:
        _clear_overrides()
    assert resp.status_code == 200
    body = resp.json()
    assert body["days"] == 3
    assert body["news_count"] == 2


def test_sentiment_empty_symbol_returns_400(client):
    _override(client=None)
    try:
        resp = client.get("/api/sentiment", params={"symbol": "   "})
    finally:
        _clear_overrides()
    assert resp.status_code == 400


def test_sentiment_missing_symbol_returns_422(client):
    resp = client.get("/api/sentiment")
    assert resp.status_code == 422
