"""memory_entries 仓储:CRUD + 过滤 + 搜索 + 计数。ADVISORY CONTEXT ONLY——
不涉任何下单/风控路径(独立自动化守卫见
tests/test_memory_advisory_isolation.py)。"""
import pytest

from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.memory_repo import add_entry, count_entries, get_entries, search_entries


@pytest.fixture
def session():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


def test_add_entry_defaults(session):
    row = add_entry(session, "insight", "title", "body text")
    assert row.id is not None
    assert row.status == "active"
    assert row.source == "manual"
    assert row.weight == 1.0
    assert row.symbol is None
    assert row.evidence_json == "{}"


def test_add_entry_with_evidence_serializes_json(session):
    row = add_entry(session, "factor", "t", "b", evidence={"windows": 2})
    assert row.evidence_json == '{"windows": 2}'


def test_add_entry_invalid_kind_raises(session):
    with pytest.raises(ValueError):
        add_entry(session, "bogus_kind", "t", "b")


def test_count_entries(session):
    assert count_entries(session) == 0
    add_entry(session, "insight", "a", "b")
    add_entry(session, "factor", "c", "d")
    assert count_entries(session) == 2


def test_get_entries_filters_by_kind(session):
    add_entry(session, "insight", "i1", "b")
    add_entry(session, "factor", "f1", "b")
    rows = get_entries(session, kind="factor")
    assert [r.title for r in rows] == ["f1"]


def test_get_entries_filters_by_symbol(session):
    add_entry(session, "trade_review", "gen", "b")
    add_entry(session, "trade_review", "aapl-specific", "b", symbol="AAPL")
    rows = get_entries(session, symbol="AAPL")
    assert [r.title for r in rows] == ["aapl-specific"]


def test_get_entries_filters_by_status(session):
    add_entry(session, "factor", "refuted-one", "b", status="refuted")
    add_entry(session, "factor", "active-one", "b")
    rows = get_entries(session, status="refuted")
    assert [r.title for r in rows] == ["refuted-one"]


def test_get_entries_no_filters_returns_all(session):
    add_entry(session, "insight", "a", "b")
    add_entry(session, "factor", "c", "d")
    assert len(get_entries(session)) == 2


def test_get_entries_orders_by_weight_desc_then_updated_at_desc(session):
    add_entry(session, "insight", "low", "b", weight=0.5)
    add_entry(session, "insight", "high", "b", weight=2.0)
    add_entry(session, "insight", "mid", "b", weight=1.0)
    rows = get_entries(session)
    assert [r.title for r in rows] == ["high", "mid", "low"]


def test_get_entries_limit(session):
    for i in range(5):
        add_entry(session, "insight", f"t{i}", "b")
    rows = get_entries(session, limit=2)
    assert len(rows) == 2


def test_search_entries_case_insensitive_title_and_body(session):
    add_entry(session, "insight", "Universe Diversification", "survivorship BIAS risk")
    add_entry(session, "insight", "unrelated", "nothing here")
    by_title = search_entries(session, "universe")
    assert len(by_title) == 1 and by_title[0].title == "Universe Diversification"
    by_body = search_entries(session, "bias")
    assert len(by_body) == 1


def test_search_entries_limit(session):
    for i in range(5):
        add_entry(session, "insight", f"matchme-{i}", "b")
    rows = search_entries(session, "matchme", limit=2)
    assert len(rows) == 2
