"""AI 荐股(committee-ranked recommendations)——在量化筛选(quant screen)基础
上对每个候选标的额外跑一次 LLM 四角色委员会,按 conviction 重新排序。全离线
(FakeProvider/FakeNews/FakeFunds/FakeGemini,不发起任何网络请求;in-memory
SQLite)。

安全红线:generate_picks 是纯分析函数——绝不调用 submit_decision,不碰
order_manager,不落库、不生成任何 DecisionRow/OrderRow/持仓变化。本文件的
no-order/no-decision 用例用"调用前后计数不变"直接证明这一点(同
tests/services/test_trade_cycle_service.py / tests/api/test_stock.py analyze
的既有证据模式)。
"""
import datetime as dt

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from app.data.base import PriceProvider, empty_bars
from app.data.fundamentals_edgar import FundamentalsProvider, FundamentalsSummary
from app.data.news_finnhub import NewsProvider
from app.screener.universe import DEFAULT_UNIVERSE
from app.services.picks_service import generate_picks
from app.store.db import init_db, make_engine, make_session_factory
from app.store.models import DecisionRow, OrderRow
from app.store.repos.paper_repo import get_positions, set_position

NOW_UTC = dt.datetime(2026, 7, 17, 16, 0, tzinfo=dt.UTC)

# DEFAULT_UNIVERSE 前几个真实标的,用作测试候选集(不必真的抓 30 只)。
CANDIDATES_3 = DEFAULT_UNIVERSE[:3]  # ["AAPL", "MSFT", "NVDA"]
CANDIDATES_5 = DEFAULT_UNIVERSE[:5]  # + ["GOOGL", "AMZN"]


class FakePicksProvider(PriceProvider):
    """只给白名单标的返回有效日线;白名单外的一律空数据,被 fetch_bars 跳过——
    把候选集收窄到测试可控的几只标的,不需要真的给全部 DEFAULT_UNIVERSE 造数据。
    """

    def __init__(self, symbols=CANDIDATES_3):
        self._symbols = set(symbols)

    def get_daily_bars(self, symbol, start, end):
        if symbol not in self._symbols or start > end:
            return empty_bars()
        idx = pd.bdate_range(start, end)
        n = len(idx)
        close = pd.Series([100.0 + 0.1 * i for i in range(n)], index=idx)
        return pd.DataFrame(
            {"open": close - 0.5, "high": close + 1.0, "low": close - 1.0, "close": close,
             "volume": 1_000_000.0},
            index=idx,
        )


class SpyFakePicksProvider(FakePicksProvider):
    """在 FakePicksProvider 之上额外给 SPY 一条单调上升行情,让
    market_regime_service.get_regime 算出 available=True/risk_on=True——用于
    证明 regime 确实被算出并喂进委员会 prompt(其余用例的白名单里没有 SPY,
    始终 available=False,这正好是"SPY 不可用时优雅降级"的隐性回归覆盖)。
    额外记录 SPY 被抓取的次数,用来证明"每轮只算一次 regime",不是每个候选
    各抓一次 SPY。
    """

    def __init__(self, symbols=CANDIDATES_3):
        super().__init__(symbols)
        self.spy_fetch_calls = 0

    def get_daily_bars(self, symbol, start, end):
        if symbol != "SPY":
            return super().get_daily_bars(symbol, start, end)
        self.spy_fetch_calls += 1
        if start > end:
            return empty_bars()
        idx = pd.date_range(start, end, freq="D")
        closes = 300.0 + 0.5 * np.arange(len(idx))
        return pd.DataFrame(
            {"open": closes, "high": closes, "low": closes, "close": closes,
             "volume": 1_000_000.0}, index=idx)


class FakePicksNews(NewsProvider):
    def get_company_news(self, symbol, start, end):
        return []


class RaisingNewsProvider(NewsProvider):
    """指定 symbol 的新闻抓取抛异常——用于证明单只候选故障不拖垮整轮生成。"""

    def __init__(self, fail_symbol: str):
        self._fail_symbol = fail_symbol

    def get_company_news(self, symbol, start, end):
        if symbol == self._fail_symbol:
            raise RuntimeError("news provider down")
        return []


class FakePicksFunds(FundamentalsProvider):
    def get_fundamentals(self, symbol):
        return FundamentalsSummary(symbol)


def _committee_json(action="hold", confidence=0.5):
    return {
        "committee": {
            "technical": {"summary": "t"}, "fundamental": {"summary": "f"},
            "sentiment": {"summary": "s"}, "bear": {"summary": "b"},
        },
        "chair": {"verdict": "verdict text", "bear_rebuttal": "rebuttal text"},
        "action": action, "confidence": confidence,
    }


class FakePicksGemini:
    """按 symbol 分派预设裁决:prompt 里嵌了 material_json 含 '"symbol": "<SYM>"'
    这一串,足以在测试候选集内唯一定位标的(候选集里没有互为子串的代码)。
    未匹配到的一律走 default(hold)。"""

    def __init__(self, by_symbol: dict | None = None, default=None):
        self._by_symbol = by_symbol or {}
        self._default = default or _committee_json("hold", 0.5)
        self.calls = 0

    def generate_json(self, prompt):
        self.calls += 1
        for sym, payload in self._by_symbol.items():
            if f'"{sym}"' in prompt:
                return payload
        return self._default


class CapturingPicksGemini(FakePicksGemini):
    """在原有按标的分派逻辑之上额外记录每次收到的 prompt 原文。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.prompts = []

    def generate_json(self, prompt):
        self.prompts.append(prompt)
        return super().generate_json(prompt)


@pytest.fixture
def session():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


# ---------------------------------------------------------------------------
# 分析only:no decision/order/position side effects; buy 排第一; held 正确
# ---------------------------------------------------------------------------


def test_generate_picks_ranks_buy_first_marks_held_and_creates_no_decision_or_order(session):
    set_position(session, "MSFT", shares=10, avg_cost=100.0)
    gemini = FakePicksGemini({"AAPL": _committee_json("buy", 0.9)})

    before_positions = dict(get_positions(session))
    before_orders = list(session.scalars(select(OrderRow)))
    before_decisions = list(session.scalars(select(DecisionRow)))

    out = generate_picks(session, FakePicksProvider(), FakePicksNews(), FakePicksFunds(),
                         gemini, now_utc=NOW_UTC, n=3)

    assert out["as_of"] == "2026-07-17"
    assert out["n"] == 3
    assert len(out["picks"]) == 3
    assert out["errors"] == []
    assert out["skipped"]  # 27 其余 DEFAULT_UNIVERSE 标的因空数据被跳过

    picks_by_symbol = {p["symbol"]: p for p in out["picks"]}
    assert out["picks"][0]["symbol"] == "AAPL"
    assert out["picks"][0]["action"] == "buy"
    assert out["picks"][0]["rank"] == 1
    assert picks_by_symbol["MSFT"]["held"] is True
    assert picks_by_symbol["AAPL"]["held"] is False
    assert picks_by_symbol["NVDA"]["held"] is False
    for p in out["picks"]:
        assert isinstance(p["quant_score"], float)
        assert "confidence" in p and "chair_verdict" in p

    assert out["gemini_calls"] == 3  # 3 个候选,全部成功调用一次委员会

    # 安全红线:纯分析——不落库、不生成任何 decision/order/持仓变化
    assert dict(get_positions(session)) == before_positions
    assert list(session.scalars(select(OrderRow))) == before_orders == []
    assert list(session.scalars(select(DecisionRow))) == before_decisions == []


def test_generate_picks_gemini_none_produces_failsafe_holds_and_zero_calls(session):
    """gemini_client=None:委员会全走 fail-safe hold,gemini_calls 计 0(没有真的
    触发任何 LLM 调用)。"""
    out = generate_picks(session, FakePicksProvider(), FakePicksNews(), FakePicksFunds(),
                         None, now_utc=NOW_UTC, n=3)
    assert len(out["picks"]) == 3
    assert all(p["action"] == "hold" for p in out["picks"])
    assert out["gemini_calls"] == 0


# ---------------------------------------------------------------------------
# 单只候选故障不拖垮整轮
# ---------------------------------------------------------------------------


def test_generate_picks_one_candidate_failure_recorded_others_still_returned(session):
    gemini = FakePicksGemini()
    out = generate_picks(session, FakePicksProvider(), RaisingNewsProvider("MSFT"),
                         FakePicksFunds(), gemini, now_utc=NOW_UTC, n=3)

    assert len(out["errors"]) == 1
    assert out["errors"][0]["symbol"] == "MSFT"
    assert {p["symbol"] for p in out["picks"]} == {"AAPL", "NVDA"}
    assert len(out["picks"]) == 2
    assert out["gemini_calls"] == 2


# ---------------------------------------------------------------------------
# 排序:buy 在前、hold 居中、sell 在后;同组内按 confidence 降序
# ---------------------------------------------------------------------------


def test_generate_picks_ranking_buy_then_hold_then_sell_by_confidence(session):
    # sell 只有在持仓时才可能出现(未持仓的 sell 会被 committee_service clamp 成
    # hold),所以给 AMZN 建一笔持仓来触发一个合法的 sell 裁决。
    set_position(session, "AMZN", shares=5, avg_cost=100.0)
    gemini = FakePicksGemini({
        "MSFT": _committee_json("buy", 0.9),
        "AAPL": _committee_json("buy", 0.6),
        "GOOGL": _committee_json("hold", 0.8),
        "NVDA": _committee_json("hold", 0.5),
        "AMZN": _committee_json("sell", 0.7),
    })

    out = generate_picks(session, FakePicksProvider(CANDIDATES_5), FakePicksNews(),
                         FakePicksFunds(), gemini, now_utc=NOW_UTC, n=5)

    assert [p["symbol"] for p in out["picks"]] == ["MSFT", "AAPL", "GOOGL", "NVDA", "AMZN"]
    assert [p["action"] for p in out["picks"]] == ["buy", "buy", "hold", "hold", "sell"]
    assert [p["rank"] for p in out["picks"]] == [1, 2, 3, 4, 5]


# ---------------------------------------------------------------------------
# market_context (macro regime): ADVISORY CONTEXT ONLY——generate_picks 在
# per-candidate 循环之外算一次 get_regime/regime_context_line
# (SpyFakePicksProvider 断言 SPY 只被抓一次,不是每个候选各抓一次),把结果原样
# 传给每次 run_committee;分析only 的行为(排序/无落库)不受影响。
# ---------------------------------------------------------------------------


def test_generate_picks_passes_market_context_to_committee_prompt(session):
    provider = SpyFakePicksProvider(CANDIDATES_3)
    gemini = CapturingPicksGemini({"AAPL": _committee_json("buy", 0.9)})

    out = generate_picks(session, provider, FakePicksNews(), FakePicksFunds(), gemini,
                         now_utc=NOW_UTC, n=3)

    assert out["errors"] == []
    assert len(out["picks"]) == 3
    assert gemini.calls == 3
    assert len(gemini.prompts) == 3
    for prompt in gemini.prompts:
        # 大盘处于 risk-on(SpyFakePicksProvider 构造的是单调上升行情)
        assert "宏观背景" in prompt
        assert "risk-on" in prompt


def test_generate_picks_regime_fetched_once_not_per_candidate(session):
    provider = SpyFakePicksProvider(CANDIDATES_3)
    gemini = FakePicksGemini()

    out = generate_picks(session, provider, FakePicksNews(), FakePicksFunds(), gemini,
                         now_utc=NOW_UTC, n=3)

    assert len(out["picks"]) == 3
    # (b) regime 只算一次并复用给本轮所有候选,不是每个候选各抓一次 SPY。
    assert provider.spy_fetch_calls == 1
