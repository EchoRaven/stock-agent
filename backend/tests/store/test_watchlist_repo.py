import datetime as dt

import pytest

from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.watchlist_repo import add, exists, list_all, remove


@pytest.fixture
def session():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


def test_add_inserts_new_row(session):
    row = add(session, "aapl", note="core holding")
    session.commit()
    assert row.id is not None
    assert row.symbol == "AAPL"
    assert row.note == "core holding"
    assert isinstance(row.added_at, dt.datetime)


def test_add_same_symbol_upserts_no_duplicate_and_updates_note(session):
    add(session, "AAPL", note="first")
    session.commit()
    row = add(session, "aapl", note="updated")
    session.commit()

    rows = list_all(session)
    assert len(rows) == 1
    assert row.note == "updated"
    assert rows[0].note == "updated"


def test_add_same_symbol_note_none_keeps_existing_note(session):
    add(session, "AAPL", note="keep me")
    session.commit()
    row = add(session, "AAPL", note=None)
    session.commit()

    assert row.note == "keep me"
    assert len(list_all(session)) == 1


def test_add_uppercases_and_strips_symbol(session):
    row = add(session, "  msft  ")
    session.commit()
    assert row.symbol == "MSFT"


def test_remove_returns_true_then_false(session):
    add(session, "AAPL")
    session.commit()

    assert remove(session, "aapl") is True
    session.commit()
    assert remove(session, "aapl") is False
    session.commit()
    assert list_all(session) == []


def test_list_all_orders_newest_first(session):
    add(session, "AAPL")
    session.commit()
    add(session, "MSFT")
    session.commit()
    add(session, "GOOG")
    session.commit()

    rows = list_all(session)
    assert [r.symbol for r in rows] == ["GOOG", "MSFT", "AAPL"]


def test_exists(session):
    assert exists(session, "AAPL") is False
    add(session, "AAPL")
    session.commit()
    assert exists(session, "aapl") is True
