"""POST /api/sentiment —— 薄壳装配测试:注入 fake news provider + fake/无 gemini,全离线。
打分/清洗逻辑本身在 tests/services/test_news_sentiment_service.py 已覆盖,这里只测装配。

安全:该端点会触发外部计费(新闻源 + 付费 Gemini)调用,因此是 token 门禁的 POST,
且 days/max_items 有界(拒绝如 days=10_000_000 这种会让 dt.timedelta 溢出、
或放大计费调用量的输入)。token 门禁覆盖见文件底部,复用
tests/api/test_security.py 的 unsecured_client 模式。
"""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.api import security
from app.api.deps import get_gemini_client, get_news_provider
from app.api.security import current_token, require_token
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
        resp = client.post("/api/sentiment", json={"symbol": "AAPL"})
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
        resp = client.post("/api/sentiment", json={"symbol": "AAPL"})
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
        resp = client.post("/api/sentiment",
                           json={"symbol": "MSFT", "days": 3, "max_items": 2})
    finally:
        _clear_overrides()
    assert resp.status_code == 200
    body = resp.json()
    assert body["days"] == 3
    assert body["news_count"] == 2


def test_sentiment_empty_symbol_returns_400(client):
    _override(client=None)
    try:
        resp = client.post("/api/sentiment", json={"symbol": "   "})
    finally:
        _clear_overrides()
    assert resp.status_code == 400


def test_sentiment_missing_symbol_returns_422(client):
    resp = client.post("/api/sentiment", json={})
    assert resp.status_code == 422


def test_sentiment_days_out_of_bounds_returns_422_not_500(client):
    # 红线:未加界的 days 会让 dt.timedelta 溢出触发未处理 500,并放大付费
    # Gemini/新闻调用量。days 上限 90,超界必须干净 422,绝不到达业务逻辑。
    _override(client=None)
    try:
        resp = client.post("/api/sentiment", json={"symbol": "AAPL", "days": 10_000_000})
    finally:
        _clear_overrides()
    assert resp.status_code == 422


def test_sentiment_unknown_field_returns_422(client):
    resp = client.post("/api/sentiment", json={"symbol": "AAPL", "bogus": 1})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# token 门禁(该端点有外部计费副作用,不能是无门禁的开放 GET/POST)。
# ---------------------------------------------------------------------------


@pytest.fixture
def token_env(tmp_path, monkeypatch):
    """把 token 文件钉在 tmp_path 下,并清掉进程级缓存,保证独立、确定性的 token。"""
    monkeypatch.setenv("STOCKAGENT_DB_PATH", str(tmp_path / "stockagent.db"))
    security._TOKEN_CACHE.clear()
    yield
    security._TOKEN_CACHE.clear()


@pytest.fixture
def unsecured_client(token_env):
    """不覆盖 require_token 的 client——唯一能证明门禁真正拦截的方式。
    news/gemini provider 仍注入 fake,保证离线且不依赖真实 provider 装配。"""
    app.dependency_overrides[get_news_provider] = lambda: FakeNewsProvider()
    app.dependency_overrides[get_gemini_client] = lambda: None
    app.dependency_overrides.pop(require_token, None)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_sentiment_without_token_is_forbidden(unsecured_client):
    resp = unsecured_client.post("/api/sentiment", json={"symbol": "AAPL"})
    assert resp.status_code == 403


def test_sentiment_with_correct_token_succeeds(unsecured_client, token_env):
    token = current_token()
    resp = unsecured_client.post("/api/sentiment", json={"symbol": "AAPL"},
                                 headers={"X-Stock-Agent-Token": token})
    assert resp.status_code == 200
    assert resp.json()["symbol"] == "AAPL"
