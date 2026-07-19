import datetime as dt

import app.cli as cli
from app.cli import build_parser, cmd_sentiment
from app.data.news_finnhub import NewsItem


class FakeNewsProvider:
    def __init__(self, items):
        self._items = items

    def get_company_news(self, symbol, start, end):
        return self._items


class FakeGemini:
    def __init__(self, result):
        self._result = result
        self.prompts = []

    def generate_json(self, prompt):
        self.prompts.append(prompt)
        return self._result


class _ExplodingGeminiClient:
    """站位:若被构造说明代码在无 key 时仍尝试建真实 GeminiClient(缺陷)。"""

    def __init__(self, *args, **kwargs):
        raise AssertionError("无 key 时不应构造真实 GeminiClient")


def _items(n=2):
    return [
        NewsItem(
            published_at=dt.date(2026, 7, 15 + i),
            headline=f"Headline {i}",
            summary=f"Summary {i}",
            source="Reuters",
            url=f"https://example.com/{i}",
        )
        for i in range(n)
    ]


def test_cmd_sentiment_prints_score_and_headlines_with_injected_fakes(capsys):
    args = build_parser().parse_args(
        ["sentiment", "AAPL", "--days", "5", "--max-items", "3"])
    provider = FakeNewsProvider(_items(2))
    gemini = FakeGemini({"sentiment": 0.8, "reason": "x"})

    rc = cmd_sentiment(args, news_provider=provider, gemini_client=gemini)

    assert rc == 0
    out = capsys.readouterr().out
    assert "情绪分: +0.800" in out
    assert "Headline 0" in out
    assert "Headline 1" in out
    assert "AAPL" in out


def test_cmd_sentiment_no_key_lists_news_without_scoring(monkeypatch, capsys):
    monkeypatch.setenv("STOCKAGENT_GEMINI_API_KEY", "")
    monkeypatch.setattr(cli, "GeminiClient", _ExplodingGeminiClient)
    provider = FakeNewsProvider(_items(2))
    args = build_parser().parse_args(["sentiment", "MSFT"])

    rc = cmd_sentiment(args, news_provider=provider, gemini_client=None)

    assert rc == 0
    out = capsys.readouterr().out
    assert "未打分" in out
    assert "未配置" in out
    assert "Headline 0" in out
    assert "Headline 1" in out


def test_cmd_sentiment_no_news_reports_zero_count(capsys):
    provider = FakeNewsProvider([])
    gemini = FakeGemini({"sentiment": 0.5})
    args = build_parser().parse_args(["sentiment", "NFLX"])

    rc = cmd_sentiment(args, news_provider=provider, gemini_client=gemini)

    assert rc == 0
    out = capsys.readouterr().out
    assert "共 0 条新闻" in out
    assert "未打分" in out
    assert "无近期新闻" in out


def test_build_parser_sentiment_defaults():
    args = build_parser().parse_args(["sentiment", "TSLA"])
    assert args.command == "sentiment"
    assert args.symbol == "TSLA"
    assert args.days == 7
    assert args.max_items == 10
    assert args.date is None


def test_build_parser_sentiment_requires_symbol():
    import pytest

    with pytest.raises(SystemExit):
        build_parser().parse_args(["sentiment"])
