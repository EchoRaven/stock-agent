"""app.cli 的 trade-cycle 子命令:装配薄壳测试(注入 fake session/provider/news/
fundamentals/gemini,离线)。编排逻辑本身在 tests/services/test_trade_cycle_service.py
已覆盖,这里只测 CLI 参数解析 + 装配 + 摘要打印。
"""
import pandas as pd
import pytest

from app.cli import build_parser, cmd_trade_cycle
from app.data.base import PriceProvider, empty_bars
from app.data.fundamentals_edgar import FundamentalsProvider, FundamentalsSummary
from app.data.news_finnhub import NewsProvider
from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.settings_repo import MODE_FULL_AUTO, set_mode


class FakeProvider(PriceProvider):
    def get_daily_bars(self, symbol, start, end):
        if start > end:
            return empty_bars()
        idx = pd.date_range(start, end, freq="D")
        return pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0,
                             "close": 100.0, "volume": 1_000_000.0}, index=idx)


class FakeNews(NewsProvider):
    def get_company_news(self, symbol, start, end):
        return []


class FakeFunds(FundamentalsProvider):
    def get_fundamentals(self, symbol):
        return FundamentalsSummary(symbol)


class FakeGemini:
    def generate_json(self, prompt):
        return {
            "committee": {
                "technical": {"summary": "t"}, "fundamental": {"summary": "f"},
                "sentiment": {"summary": "s"}, "bear": {"summary": "b"},
            },
            "chair": {"verdict": "v", "bear_rebuttal": "r"},
            "action": "hold", "confidence": 0.5,
        }


@pytest.fixture
def session():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


def test_cmd_trade_cycle_prints_summary_with_injected_fakes(session, capsys):
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    args = build_parser().parse_args(["trade-cycle", "--max-eval", "1"])

    rc = cmd_trade_cycle(args, session=session, provider=FakeProvider(),
                         news_provider=FakeNews(), fundamentals_provider=FakeFunds(),
                         gemini_client=FakeGemini())

    assert rc == 0
    out = capsys.readouterr().out
    assert "mode=full_auto" in out
    assert "hold" in out


def test_build_parser_trade_cycle_defaults():
    args = build_parser().parse_args(["trade-cycle"])
    assert args.command == "trade-cycle"
    assert args.max_eval is None
    assert args.no_settle is False


def test_build_parser_trade_cycle_flags():
    args = build_parser().parse_args(["trade-cycle", "--max-eval", "3", "--no-settle"])
    assert args.max_eval == 3
    assert args.no_settle is True
