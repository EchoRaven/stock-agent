"""screen → 四角色委员会(Gemini)→ 闸门下单 → (可选)撮合 的每日交易循环。
全离线(FakeProvider/FakeNews/FakeFunds/FakeGemini,不发起任何网络请求;
in-memory SQLite)。

覆盖五条安全属性:
(a) LLM 输出 clamp + 畸形→hold fail-safe(malformed_committee 用例)
(b) shares 服务端计算,绝不来自 LLM(buy 用例断言 shares 数值由 equity×cap 算出)
(c) 每笔交易仍经 submit_decision→RiskGate,委员会无法绕过闸门(cap 用例)
(d) news 在 committee prompt 里标为不可信(见 test_committee_service.py 的专门覆盖;
    这里的 RaisingNewsProvider 证明 briefing 材料确实经由 news_provider 这条唯一通道)
(e) advisory 模式不建单(advisory 用例)
"""
import datetime as dt

import pandas as pd
import pytest

from app.data.base import PriceProvider, empty_bars
from app.data.fundamentals_edgar import FundamentalsProvider, FundamentalsSummary
from app.data.news_finnhub import NewsProvider
from app.services.trade_cycle_service import run_trade_cycle
from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.order_repo import STATUS_REJECTED, STATUS_SUBMITTED, get_orders_by_status
from app.store.repos.paper_repo import get_account, get_positions, set_position
from app.store.repos.settings_repo import (MODE_ADVISORY, MODE_FULL_AUTO, set_mode,
                                           update_risk_params)

FIVE_SYMBOLS = ["ONE", "TWO", "THREE", "FOUR", "FIVE"]

NOW_UTC = dt.datetime(2026, 7, 17, 16, 0, tzinfo=dt.UTC)


class FakeProvider(PriceProvider):
    """离线行情源:每个 symbol 固定价格的整段日线,不发起任何网络请求。"""

    def __init__(self, prices: dict):
        self._prices = prices

    def get_daily_bars(self, symbol, start, end):
        price = self._prices.get(symbol)
        if price is None or start > end:
            return empty_bars()
        idx = pd.date_range(start, end, freq="D")
        return pd.DataFrame(
            {"open": price, "high": price + 1, "low": max(price - 1, 0.01),
             "close": price, "volume": 1_000_000.0}, index=idx)


class RaisingNewsProvider(NewsProvider):
    """指定 symbol 抓新闻时抛异常,模拟单标的材料抓取故障(不影响其余标的)。"""

    def __init__(self, bad_symbols=frozenset()):
        self._bad = frozenset(bad_symbols)

    def get_company_news(self, symbol, start, end):
        if symbol in self._bad:
            raise RuntimeError(f"news feed unavailable for {symbol}")
        return []


class FakeFunds(FundamentalsProvider):
    def get_fundamentals(self, symbol):
        return FundamentalsSummary(symbol)


def _committee_json(action, confidence=0.8):
    return {
        "committee": {
            "technical": {"summary": "t"}, "fundamental": {"summary": "f"},
            "sentiment": {"summary": "s"}, "bear": {"summary": "b"},
        },
        "chair": {"verdict": "v", "bear_rebuttal": "r"},
        "action": action, "confidence": confidence,
    }


class FakeGemini:
    """按标的返回不同裁决:在 committee_service 拼的 prompt 里按
    '"symbol": "<SYM>"' 片段匹配(committee_service._build_prompt 用
    json.dumps 内嵌 briefing symbol,格式固定)。未匹配 → default(缺省 hold)。
    """

    def __init__(self, by_symbol=None, default=None, malformed_symbols=frozenset()):
        self._by_symbol = by_symbol or {}
        self._default = default if default is not None else _committee_json("hold")
        self._malformed = frozenset(malformed_symbols)
        self.calls = 0

    def generate_json(self, prompt):
        self.calls += 1
        for sym in self._malformed:
            if f'"symbol": "{sym}"' in prompt:
                return {"not": "a valid committee response"}
        for sym, resp in self._by_symbol.items():
            if f'"symbol": "{sym}"' in prompt:
                return resp
        return self._default


@pytest.fixture
def session():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


# ---------------------------------------------------------------------------
# buy path: (a) clamp passthrough of a well-formed buy, (b) shares sized
# server-side, order actually reaches the gate and fills.
# ---------------------------------------------------------------------------


def test_full_auto_buy_creates_position_and_fill(session):
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    provider = FakeProvider({"AAPL": 100.0})
    gemini = FakeGemini(by_symbol={"AAPL": _committee_json("buy", 0.9)})

    result = run_trade_cycle(session, provider, RaisingNewsProvider(), FakeFunds(), gemini,
                             now_utc=NOW_UTC, universe=["AAPL"], max_eval=1)

    assert result["mode"] == MODE_FULL_AUTO
    assert len(result["decisions"]) == 1
    d = result["decisions"][0]
    assert d["symbol"] == "AAPL" and d["action"] == "buy"
    # (b) 服务端算股数:equity(100_000) * single_position_cap_pct(默认 0.20) // price(100)
    assert d["shares"] == 200
    assert d["submit_result"]["order"]["status"] == STATUS_SUBMITTED

    positions = get_positions(session)
    assert "AAPL" in positions and positions["AAPL"].shares == 200
    assert get_account(session, 100_000.0).cash < 100_000.0
    assert len(result["fills"]) == 1
    assert result["fills"][0]["symbol"] == "AAPL"


# ---------------------------------------------------------------------------
# sell path: held position, committee says sell → position closed.
# ---------------------------------------------------------------------------


def test_full_auto_sell_closes_held_position(session):
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    set_position(session, "MSFT", 50, 90.0)
    provider = FakeProvider({"AAPL": 100.0, "MSFT": 90.0})
    gemini = FakeGemini(by_symbol={"MSFT": _committee_json("sell", 0.9)})

    result = run_trade_cycle(session, provider, RaisingNewsProvider(), FakeFunds(), gemini,
                             now_utc=NOW_UTC, universe=["AAPL"], max_eval=None)

    msft_decision = next(d for d in result["decisions"] if d["symbol"] == "MSFT")
    assert msft_decision["action"] == "sell"
    assert msft_decision["shares"] == 50  # (b) 服务端按当前持仓算,不来自 LLM
    assert msft_decision["submit_result"]["order"]["status"] == STATUS_SUBMITTED
    assert "MSFT" not in get_positions(session)


# ---------------------------------------------------------------------------
# (e) advisory mode: decisions recorded but no orders/positions created,
# even though the committee said buy.
# ---------------------------------------------------------------------------


def test_advisory_mode_creates_no_orders(session):
    set_mode(session, MODE_ADVISORY)
    provider = FakeProvider({"AAPL": 100.0})
    gemini = FakeGemini(by_symbol={"AAPL": _committee_json("buy", 0.9)})

    result = run_trade_cycle(session, provider, RaisingNewsProvider(), FakeFunds(), gemini,
                             now_utc=NOW_UTC, universe=["AAPL"], max_eval=1)

    assert result["mode"] == MODE_ADVISORY
    d = result["decisions"][0]
    assert d["action"] == "buy"  # 建议如实记录
    assert d["submit_result"]["status"] == "recorded"
    assert "advisory" in d["submit_result"]["note"]
    assert get_positions(session) == {}
    assert get_orders_by_status(session, STATUS_SUBMITTED) == []
    assert result["fills"] == []


# ---------------------------------------------------------------------------
# (a) malformed committee output → hold fail-safe, no trade even in full_auto.
# ---------------------------------------------------------------------------


def test_malformed_committee_output_falls_back_to_hold_no_trade(session):
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    provider = FakeProvider({"AAPL": 100.0})
    gemini = FakeGemini(malformed_symbols={"AAPL"})

    result = run_trade_cycle(session, provider, RaisingNewsProvider(), FakeFunds(), gemini,
                             now_utc=NOW_UTC, universe=["AAPL"], max_eval=1)

    d = result["decisions"][0]
    assert d["action"] == "hold"
    assert d["shares"] is None
    assert get_positions(session) == {}
    assert get_orders_by_status(session, STATUS_SUBMITTED) == []


# ---------------------------------------------------------------------------
# (c) gate enforced: sizing itself respects the single-position cap, but the
# account's total-position cap is set so low that the (correctly sized) buy
# still gets rejected by RiskGate. Proves the committee cannot bypass the gate.
# ---------------------------------------------------------------------------


def test_gate_rejects_buy_exceeding_total_position_cap(session):
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    update_risk_params(session, total_position_cap_pct=0.01)
    provider = FakeProvider({"AAPL": 100.0})
    gemini = FakeGemini(by_symbol={"AAPL": _committee_json("buy", 0.9)})

    result = run_trade_cycle(session, provider, RaisingNewsProvider(), FakeFunds(), gemini,
                             now_utc=NOW_UTC, universe=["AAPL"], max_eval=1)

    d = result["decisions"][0]
    assert d["action"] == "buy"
    assert d["shares"] == 200  # sizing 没超单票上限,委员会的建议原样进了闸门
    order = d["submit_result"]["order"]
    assert order["status"] == STATUS_REJECTED
    assert "total-position cap" in order["reason"]
    assert get_positions(session) == {}
    assert result["fills"] == []
    # 循环本身照常跑完,如实汇报了这次拒绝——不是被拒就中断整轮
    assert result["errors"] == []


# ---------------------------------------------------------------------------
# one symbol's material-fetch failure doesn't abort the rest of the cycle.
# ---------------------------------------------------------------------------


def test_one_symbol_briefing_failure_does_not_abort_others(session):
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    set_position(session, "BAD", 5, 50.0)
    provider = FakeProvider({"AAPL": 100.0, "BAD": 50.0})
    gemini = FakeGemini(by_symbol={"AAPL": _committee_json("buy", 0.9)})

    result = run_trade_cycle(session, provider, RaisingNewsProvider(bad_symbols={"BAD"}),
                             FakeFunds(), gemini, now_utc=NOW_UTC, universe=["AAPL"],
                             max_eval=None)

    assert len(result["errors"]) == 1
    assert result["errors"][0]["symbol"] == "BAD"
    aapl_decision = next(d for d in result["decisions"] if d["symbol"] == "AAPL")
    assert aapl_decision["action"] == "buy"
    assert aapl_decision["submit_result"]["order"]["status"] == STATUS_SUBMITTED


# ---------------------------------------------------------------------------
# defense-in-depth: aggregate position caps (single/total) must bind
# CUMULATIVELY within one cycle. Bug: run_trade_cycle used to submit every
# decision against the SAME pre-cycle account snapshot (settle happened once,
# after the whole loop) — so N buys that each individually pass the total cap
# could jointly blow way past it. Fix: settle each submitted order immediately
# so the next symbol's gate check sees the accumulated exposure.
# ---------------------------------------------------------------------------


def test_total_cap_binds_cumulatively_within_cycle(session):
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    # max_new_positions_per_day raised so MaxNewPositionsRule (a simple count)
    # doesn't bind first and mask whether the *value* caps are cumulative.
    update_risk_params(session, max_new_positions_per_day=5,
                       single_position_cap_pct=0.20, total_position_cap_pct=0.80)
    # $150 (not a round divisor of the $20k single-position budget) leaves the
    # per-buy value ($19,950 for 133 shares) with headroom under the single-
    # position cap, so PaperBroker's fill slippage (which nudges equity down a
    # few dollars per fill) can't make SinglePositionCapRule bind first and
    # mask whether TotalPositionCapRule itself binds cumulatively.
    price = 150.0
    provider = FakeProvider({sym: price for sym in FIVE_SYMBOLS})
    gemini = FakeGemini(by_symbol={sym: _committee_json("buy", 0.9) for sym in FIVE_SYMBOLS})

    result = run_trade_cycle(session, provider, RaisingNewsProvider(), FakeFunds(), gemini,
                             now_utc=NOW_UTC, universe=FIVE_SYMBOLS, max_eval=None,
                             settle=True)

    assert result["errors"] == []
    buy_decisions = [d for d in result["decisions"] if d["action"] == "buy"]
    assert len(buy_decisions) == len(FIVE_SYMBOLS)  # sizing itself never blocks a buy
    # at least one buy must have been REJECTED by the gate on total-position-cap
    # grounds — proving the cap actually binds cumulatively, not just once.
    rejected = [d for d in buy_decisions
               if d["submit_result"]["order"]["status"] == STATUS_REJECTED]
    assert rejected, "expected at least one buy rejected by the cumulative total-position cap"
    assert any("total-position cap" in d["submit_result"]["order"]["reason"] for d in rejected)

    positions = get_positions(session)
    assert 0 < len(positions) < len(FIVE_SYMBOLS)  # not all 5 fit
    deployed = sum(p.shares * price for p in positions.values())
    account = get_account(session, 100_000.0)
    equity = account.cash + deployed
    # the whole point of the fix: total exposure stays within the 80% cap
    # (modulo float slop), NOT the ~100% the pre-fix bug over-deployed to.
    assert deployed <= 0.80 * equity + 1e-6
    assert deployed < 90_000.0


# ---------------------------------------------------------------------------
# settle=False preserved: orders stay SUBMITTED, no positions materialize —
# incremental settling must be gated on the `settle` flag exactly like the
# old single end-of-loop settle was.
# ---------------------------------------------------------------------------


def test_settle_false_leaves_orders_submitted_no_positions(session):
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    provider = FakeProvider({"AAPL": 100.0})
    gemini = FakeGemini(by_symbol={"AAPL": _committee_json("buy", 0.9)})

    result = run_trade_cycle(session, provider, RaisingNewsProvider(), FakeFunds(), gemini,
                             now_utc=NOW_UTC, universe=["AAPL"], max_eval=1, settle=False)

    d = result["decisions"][0]
    assert d["action"] == "buy"
    assert d["submit_result"]["order"]["status"] == STATUS_SUBMITTED
    assert result["fills"] == []
    assert get_positions(session) == {}
    submitted = get_orders_by_status(session, STATUS_SUBMITTED)
    assert len(submitted) == 1 and submitted[0].symbol == "AAPL"
