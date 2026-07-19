import datetime as dt
import logging

import pytest

import app.data.news_yahoo as mod
from app.data.news_finnhub import NewsItem
from app.data.news_yahoo import YahooNewsProvider

START, END = dt.date(2026, 7, 10), dt.date(2026, 7, 17)


class FakeTicker:
    def __init__(self, news):
        self._news = news

    @property
    def news(self):
        return self._news


def _entry(title="h", summary="s", pub="2026-07-15T13:16:54Z", source="Yahoo Finance",
           url="https://example.com/a", include_provider=True, include_url=True):
    content = {"title": title, "summary": summary, "pubDate": pub}
    if include_provider:
        content["provider"] = {"displayName": source}
    if include_url:
        content["canonicalUrl"] = {"url": url}
    return {"id": "1", "content": content}


def test_mapping_well_formed_entry(monkeypatch):
    raw = [_entry(title="Apple rises", summary="short summary", pub="2026-07-15T13:16:54Z",
                   source="Reuters", url="https://example.com/apple")]
    monkeypatch.setattr(mod.yf, "Ticker", lambda sym: FakeTicker(raw))
    out = YahooNewsProvider().get_company_news("AAPL", START, END)
    assert len(out) == 1
    item = out[0]
    assert isinstance(item, NewsItem)
    assert item.headline == "Apple rises"
    assert item.summary == "short summary"
    assert item.source == "Reuters"
    assert item.url == "https://example.com/apple"
    assert item.published_at == dt.date(2026, 7, 15)


def test_range_filter_excludes_out_of_range(monkeypatch):
    raw = [
        _entry(title="too old", pub="2026-07-01T00:00:00Z"),
        _entry(title="too new", pub="2026-07-20T00:00:00Z"),
        _entry(title="in range", pub="2026-07-12T00:00:00Z"),
    ]
    monkeypatch.setattr(mod.yf, "Ticker", lambda sym: FakeTicker(raw))
    out = YahooNewsProvider().get_company_news("AAPL", START, END)
    assert [n.headline for n in out] == ["in range"]


def test_sort_desc_and_max_items_cap(monkeypatch):
    raw = [_entry(title=f"h{i}", pub=f"2026-07-{10 + i:02d}T00:00:00Z") for i in range(8)]
    monkeypatch.setattr(mod.yf, "Ticker", lambda sym: FakeTicker(raw))
    out = YahooNewsProvider(max_items=3).get_company_news("AAPL", START, END)
    assert len(out) == 3
    assert [n.headline for n in out] == ["h7", "h6", "h5"]
    assert all(out[i].published_at >= out[i + 1].published_at for i in range(len(out) - 1))


def test_empty_title_skipped(monkeypatch):
    raw = [_entry(title=""), _entry(title="   "), _entry(title="kept")]
    monkeypatch.setattr(mod.yf, "Ticker", lambda sym: FakeTicker(raw))
    out = YahooNewsProvider().get_company_news("AAPL", START, END)
    assert [n.headline for n in out] == ["kept"]


def test_unparseable_or_missing_pubdate_skipped(monkeypatch):
    bad1 = _entry(title="bad date")
    bad1["content"]["pubDate"] = "not-a-date"
    bad2 = _entry(title="missing date")
    del bad2["content"]["pubDate"]
    good = _entry(title="good date", pub="2026-07-12T00:00:00Z")
    monkeypatch.setattr(mod.yf, "Ticker", lambda sym: FakeTicker([bad1, bad2, good]))
    out = YahooNewsProvider().get_company_news("AAPL", START, END)
    assert [n.headline for n in out] == ["good date"]


def test_missing_provider_defaults_source(monkeypatch):
    raw = [_entry(title="no provider", include_provider=False, pub="2026-07-12T00:00:00Z")]
    monkeypatch.setattr(mod.yf, "Ticker", lambda sym: FakeTicker(raw))
    out = YahooNewsProvider().get_company_news("AAPL", START, END)
    assert out[0].source == "Yahoo Finance"


def test_missing_url_defaults_empty(monkeypatch):
    raw = [_entry(title="no url", include_url=False, pub="2026-07-12T00:00:00Z")]
    monkeypatch.setattr(mod.yf, "Ticker", lambda sym: FakeTicker(raw))
    out = YahooNewsProvider().get_company_news("AAPL", START, END)
    assert out[0].url == ""


def test_ticker_raising_returns_empty(monkeypatch, caplog):
    def boom(sym):
        raise RuntimeError("network down")

    monkeypatch.setattr(mod.yf, "Ticker", boom)
    with caplog.at_level(logging.WARNING):
        out = YahooNewsProvider().get_company_news("AAPL", START, END)
    assert out == []
    assert "yahoo" in caplog.text.lower()


def test_news_none_returns_empty(monkeypatch):
    monkeypatch.setattr(mod.yf, "Ticker", lambda sym: FakeTicker(None))
    out = YahooNewsProvider().get_company_news("AAPL", START, END)
    assert out == []


def test_news_empty_list_returns_empty(monkeypatch):
    monkeypatch.setattr(mod.yf, "Ticker", lambda sym: FakeTicker([]))
    out = YahooNewsProvider().get_company_news("AAPL", START, END)
    assert out == []


@pytest.mark.network
def test_yahoo_news_real_smoke():
    """真实联网:pytest -m network 手动运行。容忍空列表(Yahoo 可能无最新新闻)。"""
    today = dt.date.today()
    out = YahooNewsProvider().get_company_news("AAPL", today - dt.timedelta(days=30), today + dt.timedelta(days=1))
    assert isinstance(out, list)
    for item in out:
        assert isinstance(item, NewsItem)
        assert item.headline != ""
        assert isinstance(item.published_at, dt.date)
