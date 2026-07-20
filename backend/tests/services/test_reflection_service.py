"""reflection_service(Phase 2):平仓复盘。全离线(in-memory SQLite,不发起任何
网络请求)。覆盖四条安全属性:
(a) 已实现盈亏由均价法从成交流水确定性算出,从不采信 LLM 的任何数字;
(b) 幂等——以 sell_fill_id 为键,重复调用不产生重复的 trade_review 条目;
(c) advisory-only——只经 memory_repo 读写,见 tests/test_memory_advisory_isolation.py
    的独立静态/运行期守卫(本文件不重复那套扫描,只验证行为);
(d) LLM 教训完全可选——gemini_client=None、调用异常、响应畸形都不影响事实性
    复盘本身落库。
"""
import datetime as dt
import json

import pytest

from app.services.reflection_service import reconstruct_closed_trades, reflect_on_closed_trades
from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.decision_repo import save_decision
from app.store.repos.memory_repo import get_entries
from app.store.repos.paper_repo import add_fill

D1 = dt.date(2026, 6, 1)
D2 = dt.date(2026, 6, 2)
D3 = dt.date(2026, 6, 10)
D4 = dt.date(2026, 6, 20)


@pytest.fixture
def session():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


def _committee_payload(action: str, verdict: str) -> str:
    return json.dumps({
        "symbol": "AAPL", "as_of": D1.isoformat(), "action": action, "confidence": 0.8,
        "committee": {
            "technical": {"summary": "t"}, "fundamental": {"summary": "f"},
            "sentiment": {"summary": "s"}, "bear": {"summary": "b"},
        },
        "chair": {"verdict": verdict, "bear_rebuttal": "r"},
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# reconstruct_closed_trades: 均价法确定性算出的已实现盈亏(不涉及 LLM)。
# ---------------------------------------------------------------------------


def test_reconstruct_no_sells_returns_empty(session):
    add_fill(session, 1, D1, "AAPL", "buy", 100, 10.0)
    assert reconstruct_closed_trades(session) == []


def test_reconstruct_avgcost_worked_example_two_lots_then_full_close(session):
    # buy 100@10 + buy 100@12 -> avg 11; sell 100@15 -> realized 400 (partial,
    # still holding 100); then sell 100@9 -> realized -200, fully closed.
    add_fill(session, 1, D1, "AAPL", "buy", 100, 10.0)
    add_fill(session, 1, D1, "AAPL", "buy", 100, 12.0)
    add_fill(session, 2, D2, "AAPL", "sell", 100, 15.0)
    add_fill(session, 3, D3, "AAPL", "sell", 100, 9.0)

    closed = reconstruct_closed_trades(session)
    assert len(closed) == 2

    first, second = closed
    assert first["symbol"] == "AAPL"
    assert first["shares"] == 100
    assert first["buy_vwap"] == pytest.approx(11.0)
    assert first["sell_price"] == pytest.approx(15.0)
    assert first["sell_date"] == D2
    assert first["realized_pnl"] == pytest.approx(400.0)
    assert first["realized_pnl_pct"] == pytest.approx(400.0 / 1100.0 * 100)
    assert first["holding_days"] == (D2 - D1).days
    assert first["fully_closed"] is False

    assert second["symbol"] == "AAPL"
    assert second["shares"] == 100
    assert second["buy_vwap"] == pytest.approx(11.0)  # avg cost unchanged by sells
    assert second["sell_price"] == pytest.approx(9.0)
    assert second["sell_date"] == D3
    assert second["realized_pnl"] == pytest.approx(-200.0)
    assert second["holding_days"] == (D3 - D1).days
    assert second["fully_closed"] is True

    assert first["sell_fill_id"] != second["sell_fill_id"]


def test_reconstruct_oversell_clamps_running_shares_at_zero(session):
    add_fill(session, 1, D1, "AAPL", "buy", 50, 10.0)
    add_fill(session, 2, D2, "AAPL", "sell", 100, 12.0)  # over-sell bad data
    closed = reconstruct_closed_trades(session)
    assert len(closed) == 1
    assert closed[0]["fully_closed"] is True  # clamped to 0, never negative


def test_reconstruct_separates_by_symbol(session):
    add_fill(session, 1, D1, "AAPL", "buy", 10, 10.0)
    add_fill(session, 2, D1, "MSFT", "buy", 10, 20.0)
    add_fill(session, 3, D2, "AAPL", "sell", 10, 11.0)
    add_fill(session, 4, D2, "MSFT", "sell", 10, 22.0)
    closed = reconstruct_closed_trades(session)
    symbols = {c["symbol"] for c in closed}
    assert symbols == {"AAPL", "MSFT"}
    aapl = next(c for c in closed if c["symbol"] == "AAPL")
    msft = next(c for c in closed if c["symbol"] == "MSFT")
    assert aapl["realized_pnl"] == pytest.approx(10.0)
    assert msft["realized_pnl"] == pytest.approx(20.0)


# ---------------------------------------------------------------------------
# reflect_on_closed_trades: 写 trade_review 条目,facts-first。
# ---------------------------------------------------------------------------


def test_reflect_writes_trade_review_with_correct_pnl_evidence(session):
    add_fill(session, 1, D1, "AAPL", "buy", 100, 10.0)
    add_fill(session, 1, D1, "AAPL", "buy", 100, 12.0)
    add_fill(session, 2, D2, "AAPL", "sell", 100, 15.0)

    created = reflect_on_closed_trades(session, gemini_client=None)
    assert len(created) == 1
    entry = created[0]
    assert entry["kind"] == "trade_review"
    assert entry["symbol"] == "AAPL"
    assert entry["source"] == "reflection"

    evidence = json.loads(entry["evidence_json"])
    assert evidence["realized_pnl"] == pytest.approx(400.0)
    assert evidence["realized_pnl_pct"] == pytest.approx(400.0 / 1100.0 * 100)
    assert evidence["buy_vwap"] == pytest.approx(11.0)
    assert evidence["sell_price"] == pytest.approx(15.0)
    assert evidence["shares"] == 100
    assert "sell_fill_id" in evidence

    rows = get_entries(session, kind="trade_review")
    assert len(rows) == 1
    assert "AAPL" in rows[0].title
    assert "400" in rows[0].body


def test_reflect_is_idempotent_second_call_creates_no_duplicates(session):
    add_fill(session, 1, D1, "AAPL", "buy", 100, 10.0)
    add_fill(session, 2, D2, "AAPL", "sell", 100, 15.0)

    first = reflect_on_closed_trades(session, gemini_client=None)
    assert len(first) == 1

    second = reflect_on_closed_trades(session, gemini_client=None)
    assert second == []

    rows = get_entries(session, kind="trade_review")
    assert len(rows) == 1  # no duplicate


def test_reflect_new_sell_after_prior_review_only_adds_the_new_one(session):
    add_fill(session, 1, D1, "AAPL", "buy", 100, 10.0)
    add_fill(session, 2, D2, "AAPL", "sell", 50, 15.0)
    first = reflect_on_closed_trades(session, gemini_client=None)
    assert len(first) == 1

    add_fill(session, 3, D3, "AAPL", "sell", 50, 9.0)
    second = reflect_on_closed_trades(session, gemini_client=None)
    assert len(second) == 1
    assert second[0]["symbol"] == "AAPL"

    rows = get_entries(session, kind="trade_review")
    assert len(rows) == 2


def test_reflect_with_no_closed_trades_returns_empty(session):
    add_fill(session, 1, D1, "AAPL", "buy", 100, 10.0)
    assert reflect_on_closed_trades(session, gemini_client=None) == []
    assert get_entries(session, kind="trade_review") == []


def test_reflect_works_with_gemini_client_none_facts_only(session):
    add_fill(session, 1, D1, "AAPL", "buy", 100, 10.0)
    add_fill(session, 2, D2, "AAPL", "sell", 100, 15.0)

    created = reflect_on_closed_trades(session, gemini_client=None)
    assert len(created) == 1
    # no LLM lesson appended: body ends right after the sell-rationale/PnL sentence
    assert created[0]["body"].endswith("。")


# ---------------------------------------------------------------------------
# rationale pulling (best-effort, from decision_repo chair.verdict).
# ---------------------------------------------------------------------------


def test_reflect_pulls_buy_and_sell_rationale_when_decisions_exist(session):
    add_fill(session, 1, D1, "AAPL", "buy", 100, 10.0)
    add_fill(session, 2, D2, "AAPL", "sell", 100, 15.0)
    save_decision(session, D1, "AAPL", "buy", 0.8, "full_auto",
                  _committee_payload("buy", "基本面拐点,建仓"))
    save_decision(session, D2, "AAPL", "sell", 0.8, "full_auto",
                  _committee_payload("sell", "涨幅已达目标位,止盈"))

    created = reflect_on_closed_trades(session, gemini_client=None)
    body = created[0]["body"]
    assert "买入理由:基本面拐点,建仓。" in body
    assert "卖出理由:涨幅已达目标位,止盈。" in body


def test_reflect_omits_rationale_gracefully_when_absent(session):
    add_fill(session, 1, D1, "AAPL", "buy", 100, 10.0)
    add_fill(session, 2, D2, "AAPL", "sell", 100, 15.0)
    # no decisions seeded at all
    created = reflect_on_closed_trades(session, gemini_client=None)
    body = created[0]["body"]
    assert "买入理由" not in body
    assert "卖出理由" not in body
    assert "AAPL 平仓复盘" in body  # facts still present


# ---------------------------------------------------------------------------
# optional LLM lesson: success appends it; any failure mode leaves facts intact.
# ---------------------------------------------------------------------------


class _FakeGeminiLesson:
    def __init__(self, lesson="止盈过早,应分批"):
        self._lesson = lesson
        self.calls = 0

    def generate_json(self, prompt):
        self.calls += 1
        # facts-only prompt: must not contain any rationale text verbatim as a
        # "number" - just sanity check the prompt carries the real pnl figure.
        assert "AAPL" in prompt
        return {"lesson": self._lesson}


def test_reflect_appends_llm_lesson_when_gemini_succeeds(session):
    add_fill(session, 1, D1, "AAPL", "buy", 100, 10.0)
    add_fill(session, 2, D2, "AAPL", "sell", 100, 15.0)
    gemini = _FakeGeminiLesson("止盈过早,应分批离场")

    created = reflect_on_closed_trades(session, gemini_client=gemini)
    assert gemini.calls == 1
    assert "止盈过早,应分批离场" in created[0]["body"]


class _RaisingGemini:
    def generate_json(self, prompt):
        raise RuntimeError("network exploded")


def test_reflect_llm_exception_does_not_block_facts(session):
    add_fill(session, 1, D1, "AAPL", "buy", 100, 10.0)
    add_fill(session, 2, D2, "AAPL", "sell", 100, 15.0)

    created = reflect_on_closed_trades(session, gemini_client=_RaisingGemini())
    assert len(created) == 1
    evidence = json.loads(created[0]["evidence_json"])
    assert evidence["realized_pnl"] == pytest.approx(500.0)  # 100 * (15 - 10)
    assert "AAPL 平仓复盘" in created[0]["body"]


class _MalformedGemini:
    def generate_json(self, prompt):
        return {"not": "a lesson"}


def test_reflect_llm_malformed_response_does_not_block_facts(session):
    add_fill(session, 1, D1, "AAPL", "buy", 100, 10.0)
    add_fill(session, 2, D2, "AAPL", "sell", 100, 15.0)

    created = reflect_on_closed_trades(session, gemini_client=_MalformedGemini())
    assert len(created) == 1
    assert created[0]["body"].endswith("。")  # no trailing lesson text appended


def test_reflect_llm_none_response_does_not_block_facts(session):
    add_fill(session, 1, D1, "AAPL", "buy", 100, 10.0)
    add_fill(session, 2, D2, "AAPL", "sell", 100, 15.0)

    class _NoneGemini:
        def generate_json(self, prompt):
            return None

    created = reflect_on_closed_trades(session, gemini_client=_NoneGemini())
    assert len(created) == 1
    assert created[0]["body"].endswith("。")
