"""memory_service:6 条真实实验结论种子的幂等播种 + 委员会上下文组装。
ADVISORY CONTEXT ONLY——纯只读检索,全离线(in-memory SQLite,不发起任何
网络请求)。"""
import datetime as dt
import json

import pytest

from app.services.memory_service import SEED_ENTRIES, ensure_seeded, get_committee_context
from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.decision_repo import save_decision
from app.store.repos.memory_repo import count_entries, get_entries

D = dt.date(2026, 7, 20)


@pytest.fixture
def session():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


# ---------------------------------------------------------------------------
# ensure_seeded: 6 条真实结论,一次性、幂等。
# ---------------------------------------------------------------------------


def test_seed_entries_has_six_real_conclusions():
    assert len(SEED_ENTRIES) == 6
    kinds = [e["kind"] for e in SEED_ENTRIES]
    assert kinds.count("factor") == 4
    assert kinds.count("insight") == 2
    assert all(e["source"] == "seed_experiment" for e in SEED_ENTRIES)
    # 4 条 factor 结论均已被证伪(refuted)——这是真实回测的结论,不是编造的正面结果
    factor_statuses = [e["status"] for e in SEED_ENTRIES if e["kind"] == "factor"]
    assert factor_statuses == ["refuted"] * 4


def test_ensure_seeded_inserts_six_entries_once(session):
    assert count_entries(session) == 0
    inserted = ensure_seeded(session)
    assert inserted == 6
    assert count_entries(session) == 6
    rows = get_entries(session, status="refuted")
    assert len(rows) == 4  # 4 条被证伪的 factor 结论


def test_ensure_seeded_is_idempotent(session):
    ensure_seeded(session)
    second = ensure_seeded(session)
    assert second == 0
    assert count_entries(session) == 6  # 没有重复插入


def test_seeded_entries_carry_evidence(session):
    ensure_seeded(session)
    rows = get_entries(session, kind="factor")
    assert all(json.loads(r.evidence_json) for r in rows)


# ---------------------------------------------------------------------------
# get_committee_context
# ---------------------------------------------------------------------------


def test_get_committee_context_seeds_lazily_on_empty_store(session):
    assert count_entries(session) == 0
    ctx = get_committee_context(session, "AAPL")
    assert count_entries(session) == 6  # 惰性播种发生了
    assert ctx  # 播种后一定有知识可返回


def test_get_committee_context_contains_insight_and_is_labeled_internal(session):
    ctx = get_committee_context(session, "AAPL")
    assert "元结论" in ctx  # 种子里的一条 insight
    assert "【已积累的知识/教训(内部,仅供参考)】" in ctx


def test_get_committee_context_includes_prior_decision(session):
    save_decision(session, D, "AAPL", "buy", 0.75, "advisory",
                  json.dumps({"chair": {"verdict": "基本面稳健,小仓位试探",
                                        "bear_rebuttal": "r"}}))
    ctx = get_committee_context(session, "AAPL")
    assert "【本票历史决策】" in ctx
    assert "2026-07-20 buy conf0.75" in ctx
    assert "基本面稳健,小仓位试探" in ctx


def test_get_committee_context_decision_for_other_symbol_not_included(session):
    save_decision(session, D, "MSFT", "sell", 0.6, "advisory", json.dumps({}))
    ctx = get_committee_context(session, "AAPL")
    assert "【本票历史决策】" not in ctx


def test_get_committee_context_max_insights_caps_knowledge_lines(session):
    ctx = get_committee_context(session, "AAPL", max_insights=2)
    knowledge_lines = [
        line for line in ctx.splitlines()
        if line.startswith("- [") and "conf" not in line
    ]
    assert len(knowledge_lines) == 2


def test_get_committee_context_max_decisions_caps_decision_lines(session):
    for i in range(5):
        save_decision(session, D - dt.timedelta(days=i), "AAPL", "hold", 0.5, "advisory",
                      json.dumps({}))
    ctx = get_committee_context(session, "AAPL", max_decisions=2)
    decision_lines = [line for line in ctx.splitlines() if line.startswith("- ") and "conf" in line]
    assert len(decision_lines) == 2


def test_get_committee_context_malformed_decision_payload_does_not_crash(session):
    save_decision(session, D, "AAPL", "hold", 0.5, "advisory", "not valid json")
    ctx = get_committee_context(session, "AAPL")
    assert "2026-07-20 hold conf0.50" in ctx
