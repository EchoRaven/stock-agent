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

import numpy as np
import pandas as pd
import pytest

from app.data.base import PriceProvider, empty_bars
from app.data.fundamentals_edgar import FundamentalsProvider, FundamentalsSummary
from app.data.news_finnhub import NewsProvider
from app.services.trade_cycle_service import run_trade_cycle
from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.order_repo import (STATUS_REJECTED, STATUS_SUBMITTED, STATUSES,
                                        create_order, get_orders_by_status)
from app.store.repos.paper_repo import add_fill, get_account, get_positions, set_position
from app.store.repos.settings_repo import (MODE_ADVISORY, MODE_FULL_AUTO, set_mode,
                                           update_risk_params)
from app.util.trading_day import et_trading_day

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


class PreMarketProvider(FakeProvider):
    """模拟盘前:当日(end)K线尚未发布,只有到 end-1 的历史 → 当日开盘价缺失。"""

    def get_daily_bars(self, symbol, start, end):
        return super().get_daily_bars(symbol, start, end - dt.timedelta(days=1))


class SpyFakeProvider(FakeProvider):
    """在 FakeProvider 之上额外给 SPY 一条单调上升行情,让
    market_regime_service.get_regime 算出 available=True/risk_on=True——用于
    证明 regime 确实被算出并喂进委员会 prompt(而不是像其余用例那样因为
    fake provider 压根没有 SPY 数据而始终 available=False)。额外记录 SPY 被
    抓取的次数,用来证明"整轮循环只算一次 regime",不是每只标的各抓一次 SPY。
    """

    def __init__(self, prices: dict):
        super().__init__(prices)
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
# Phase 2 wiring: after settle, closed positions get an automatic post-mortem
# written into memory (ADVISORY CONTEXT ONLY, never touches the gate/order
# path itself — see app/services/reflection_service.py).
# ---------------------------------------------------------------------------


def test_full_auto_cycle_writes_trade_review_after_position_closes(session):
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    provider = FakeProvider({"AAPL": 100.0})

    buy_gemini = FakeGemini(by_symbol={"AAPL": _committee_json("buy", 0.9)})
    buy_result = run_trade_cycle(session, provider, RaisingNewsProvider(), FakeFunds(),
                                 buy_gemini, now_utc=NOW_UTC, universe=["AAPL"], max_eval=1)
    assert buy_result["trade_reviews"] == []  # nothing closed yet, still holding

    later = NOW_UTC + dt.timedelta(days=3)
    sell_gemini = FakeGemini(by_symbol={"AAPL": _committee_json("sell", 0.9)})
    sell_result = run_trade_cycle(session, provider, RaisingNewsProvider(), FakeFunds(),
                                  sell_gemini, now_utc=later, universe=["AAPL"], max_eval=1)

    assert len(sell_result["trade_reviews"]) == 1
    review = sell_result["trade_reviews"][0]
    assert review["kind"] == "trade_review"
    assert review["symbol"] == "AAPL"

    from app.store.repos.memory_repo import get_entries
    rows = get_entries(session, kind="trade_review")
    assert len(rows) == 1
    assert rows[0].symbol == "AAPL"
    assert rows[0].source == "reflection"

    # re-running a no-op cycle (nothing new to close) must not duplicate it.
    noop_gemini = FakeGemini(default=_committee_json("hold", 0.5))
    noop_result = run_trade_cycle(session, provider, RaisingNewsProvider(), FakeFunds(),
                                  noop_gemini, now_utc=later + dt.timedelta(days=1),
                                  universe=["AAPL"], max_eval=1)
    assert noop_result["trade_reviews"] == []
    assert len(get_entries(session, kind="trade_review")) == 1


def test_premarket_fills_at_last_close_when_open_unavailable(session):
    # 盘前跑一轮:当日开盘价尚未发布,应回退到最近收盘价成交,而非"无开盘价"被撤单。
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    provider = PreMarketProvider({"AAPL": 100.0})
    gemini = FakeGemini(by_symbol={"AAPL": _committee_json("buy", 0.9)})

    result = run_trade_cycle(session, provider, RaisingNewsProvider(), FakeFunds(), gemini,
                             now_utc=NOW_UTC, universe=["AAPL"], max_eval=1)

    positions = get_positions(session)
    assert "AAPL" in positions and positions["AAPL"].shares == 200  # 按最近收盘价 100 成交
    assert len(result["fills"]) == 1
    assert get_account(session, 100_000.0).cash < 100_000.0


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


# ---------------------------------------------------------------------------
# memory_context: ADVISORY CONTEXT ONLY——run_trade_cycle 在调用 run_committee
# 前算出 get_committee_context(session, symbol) 并原样传入;full_auto 一轮照常
# 跑完、下单闸门不受影响,委员会拿到的 prompt 里能看到种子知识文本。
# ---------------------------------------------------------------------------


class CapturingGemini(FakeGemini):
    """在原有按标的分派逻辑之上额外记录每次收到的 prompt 原文。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.prompts = []

    def generate_json(self, prompt):
        self.prompts.append(prompt)
        return super().generate_json(prompt)


def test_full_auto_cycle_passes_memory_context_to_committee_prompt(session):
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    provider = FakeProvider({"AAPL": 100.0})
    gemini = CapturingGemini(by_symbol={"AAPL": _committee_json("buy", 0.9)})

    result = run_trade_cycle(session, provider, RaisingNewsProvider(), FakeFunds(), gemini,
                             now_utc=NOW_UTC, universe=["AAPL"], max_eval=1)

    assert gemini.calls == 1
    assert len(gemini.prompts) == 1
    # 种子知识里的一条真实结论短语,证明 memory_context 确实被拼进了委员会 prompt
    assert "元结论" in gemini.prompts[0]
    assert "【已积累的知识/教训(内部,仅供参考)】" in gemini.prompts[0]
    assert result["decisions"][0]["action"] == "buy"  # 闸门/下单路径不受影响
    assert result["decisions"][0]["submit_result"]["order"]["status"] == STATUS_SUBMITTED


# ---------------------------------------------------------------------------
# market_context (macro regime): ADVISORY CONTEXT ONLY——run_trade_cycle 在
# per-symbol 循环之外算一次 get_regime/regime_context_line(SpyFakeProvider 断言
# SPY 只被抓一次,不是每只标的各抓一次),把结果原样传给每次 run_committee;
# full_auto 一轮照常跑完、下单闸门不受影响。
# ---------------------------------------------------------------------------


def test_full_auto_cycle_passes_market_context_to_committee_prompt(session):
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    provider = SpyFakeProvider({"AAPL": 100.0})
    gemini = CapturingGemini(by_symbol={"AAPL": _committee_json("buy", 0.9)})

    result = run_trade_cycle(session, provider, RaisingNewsProvider(), FakeFunds(), gemini,
                             now_utc=NOW_UTC, universe=["AAPL"], max_eval=1)

    assert gemini.calls == 1
    assert len(gemini.prompts) == 1
    # 大盘处于 risk-on(SpyFakeProvider 构造的是单调上升行情)
    assert "宏观背景" in gemini.prompts[0]
    assert "risk-on" in gemini.prompts[0]
    assert result["decisions"][0]["action"] == "buy"  # 闸门/下单路径不受影响
    assert result["decisions"][0]["submit_result"]["order"]["status"] == STATUS_SUBMITTED


def test_market_regime_fetched_once_per_cycle_not_per_symbol(session):
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    provider = SpyFakeProvider({"AAPL": 100.0, "MSFT": 90.0})
    gemini = FakeGemini(default=_committee_json("hold", 0.5))

    run_trade_cycle(session, provider, RaisingNewsProvider(), FakeFunds(), gemini,
                    now_utc=NOW_UTC, universe=["AAPL", "MSFT"], max_eval=None)

    # (b) regime 只算一次并复用给本轮所有标的,不是每只标的各抓一次 SPY。
    assert provider.spy_fetch_calls == 1


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


# ---------------------------------------------------------------------------
# capacity-aware pre-filter (perf, MEASURED): once the daily new-position
# budget is exhausted / a symbol is in its post-sell cooldown, that BUY is
# already deterministically impossible at the gate (MaxNewPositionsRule /
# CooldownRule in app/risk/rules.py) — skip it BEFORE the LLM committee call.
# This is a pure cost optimization: it may only ever remove work, never
# permit/create/resize an order, and must never skip a held symbol (the
# committee may still say sell for it). See _capacity_impossible_filter.
# ---------------------------------------------------------------------------


def _seed_counted_buys(session, as_of, symbols):
    """按 buy_symbols_today 的计数口径直接造 N 张已计数的活跃买单——不必真的跑一遍
    完整成交流水线,只为让"当日新开仓配额已用满"这个前置状态可控可复现。"""
    for sym in symbols:
        create_order(session, as_of, sym, "buy", 10, STATUS_SUBMITTED, MODE_FULL_AUTO)


def test_capacity_reached_skips_non_held_candidates_before_llm_call(session):
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    as_of = et_trading_day(NOW_UTC)
    # exhaust today's max_new_positions_per_day (default 3) with 3 unrelated symbols
    _seed_counted_buys(session, as_of, ["X1", "X2", "X3"])
    set_position(session, "HELD", 10, 90.0)
    provider = FakeProvider({"HELD": 90.0, "NEW1": 100.0, "NEW2": 110.0, "NEW3": 120.0})
    gemini = FakeGemini(default=_committee_json("hold", 0.5))

    result = run_trade_cycle(session, provider, RaisingNewsProvider(), FakeFunds(), gemini,
                             now_utc=NOW_UTC, universe=["NEW1", "NEW2", "NEW3"], max_eval=None)

    # MEASURED reduction: without the filter this cycle evaluates 4 symbols (3
    # non-held candidates + 1 held) => 4 Gemini calls. With the filter, only the
    # held symbol is evaluated => 1 call. 3/4 (75%) of the LLM calls this cycle
    # would have burned on structurally-impossible buys are avoided.
    assert gemini.calls == 1
    skipped_symbols = {s["symbol"] for s in result["skipped_no_capacity"]}
    assert skipped_symbols == {"NEW1", "NEW2", "NEW3"}
    for entry in result["skipped_no_capacity"]:
        assert "max new positions per day (3) reached" in entry["reason"]
    decided_symbols = {d["symbol"] for d in result["decisions"]}
    assert decided_symbols == {"HELD"}
    all_orders = []
    for status in STATUSES:
        all_orders.extend(get_orders_by_status(session, status))
    assert not any(o.symbol in {"NEW1", "NEW2", "NEW3"} for o in all_orders)


def test_held_symbol_never_skipped_at_zero_capacity_and_can_still_sell(session):
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    as_of = et_trading_day(NOW_UTC)
    _seed_counted_buys(session, as_of, ["X1", "X2", "X3"])  # capacity exhausted
    set_position(session, "HELD", 10, 90.0)
    provider = FakeProvider({"HELD": 90.0})
    gemini = FakeGemini(by_symbol={"HELD": _committee_json("sell", 0.9)})

    result = run_trade_cycle(session, provider, RaisingNewsProvider(), FakeFunds(), gemini,
                             now_utc=NOW_UTC, universe=["HELD"], max_eval=None)

    assert result["skipped_no_capacity"] == []  # held: capacity is irrelevant to a sell
    held_decision = next(d for d in result["decisions"] if d["symbol"] == "HELD")
    assert held_decision["action"] == "sell"
    assert held_decision["submit_result"]["order"]["status"] == STATUS_SUBMITTED
    assert "HELD" not in get_positions(session)


def test_capacity_available_skips_nothing(session):
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    provider = FakeProvider({"NEW1": 100.0, "NEW2": 110.0})
    gemini = FakeGemini(default=_committee_json("hold", 0.5))

    result = run_trade_cycle(session, provider, RaisingNewsProvider(), FakeFunds(), gemini,
                             now_utc=NOW_UTC, universe=["NEW1", "NEW2"], max_eval=None)

    assert result["skipped_no_capacity"] == []
    assert gemini.calls == 2
    assert {d["symbol"] for d in result["decisions"]} == {"NEW1", "NEW2"}


def test_cooldown_blocked_non_held_symbol_skipped_even_with_capacity(session):
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    as_of = et_trading_day(NOW_UTC)
    last_sell_date = as_of - dt.timedelta(days=2)  # default cooldown_days=5 → still cooling
    # NOTE: this stray sell fill (no matching buy) also makes
    # reflect_on_closed_trades emit an *unrelated* extra LLM call for its
    # post-mortem lesson after the loop (pre-existing reflection behavior,
    # nothing to do with the capacity filter) — so we assert on the captured
    # committee PROMPTS (never contains COOLED's briefing) rather than the
    # raw call count, to keep this test's proof of "no LLM call for COOLED"
    # free of that confound.
    add_fill(session, 999, last_sell_date, "COOLED", "sell", 10, 50.0)
    provider = FakeProvider({"COOLED": 55.0, "FRESH": 100.0})
    gemini = CapturingGemini(default=_committee_json("hold", 0.5))

    result = run_trade_cycle(session, provider, RaisingNewsProvider(), FakeFunds(), gemini,
                             now_utc=NOW_UTC, universe=["COOLED", "FRESH"], max_eval=None)

    skipped = {s["symbol"]: s["reason"] for s in result["skipped_no_capacity"]}
    assert set(skipped) == {"COOLED"}
    assert skipped["COOLED"] == f"cooldown: sold on {last_sell_date}, 5-day cooldown active"
    assert {d["symbol"] for d in result["decisions"]} == {"FRESH"}
    # exclude the unrelated post-mortem "lesson" prompt (fired for the stray
    # COOLED sell fill by reflect_on_closed_trades, see NOTE above) — it also
    # happens to json.dumps a "symbol" field, which would otherwise confound
    # a plain substring check.
    committee_prompts = [p for p in gemini.prompts if "已平仓模拟股票交易" not in p]
    assert committee_prompts, "expected at least one real committee prompt (FRESH)"
    assert not any('"symbol": "COOLED"' in p for p in committee_prompts)
    assert any('"symbol": "FRESH"' in p for p in committee_prompts)


def test_capacity_prefilter_read_failure_degrades_to_evaluating_everything(session, monkeypatch):
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    as_of = et_trading_day(NOW_UTC)
    _seed_counted_buys(session, as_of, ["X1", "X2", "X3"])  # would otherwise exhaust capacity

    def _raise(*args, **kwargs):
        raise RuntimeError("boom: repo unavailable")

    monkeypatch.setattr("app.services.trade_cycle_service.buy_symbols_today", _raise)

    provider = FakeProvider({"NEW1": 100.0})
    gemini = FakeGemini(default=_committee_json("hold", 0.5))

    result = run_trade_cycle(session, provider, RaisingNewsProvider(), FakeFunds(), gemini,
                             now_utc=NOW_UTC, universe=["NEW1"], max_eval=None)

    # fail-safe: pre-check blew up → skip nothing, evaluate everything (today's behavior)
    assert result["skipped_no_capacity"] == []
    assert gemini.calls == 1
    assert result["decisions"][0]["symbol"] == "NEW1"
