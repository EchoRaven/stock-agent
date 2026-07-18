import datetime as dt

from sqlalchemy import inspect, select

from app.store.db import init_db, make_engine, make_session_factory
from app.store.models import (AlertRow, HeartbeatRow, OrderRow, PaperAccountRow,
                              PaperFillRow, PaperPositionRow, SettingsRow)


def _session():
    engine = make_engine(":memory:")
    init_db(engine)
    return make_session_factory(engine)()


def test_init_db_creates_m3_tables():
    engine = make_engine(":memory:")
    init_db(engine)
    tables = set(inspect(engine).get_table_names())
    assert {"settings", "orders", "paper_account", "paper_positions",
            "paper_fills", "heartbeats", "alerts"} <= tables


def test_settings_row_defaults():
    with _session() as session:
        session.add(SettingsRow(id=1))
        session.flush()
        row = session.get(SettingsRow, 1)
        assert row.mode == "advisory"
        assert row.single_position_cap_pct == 0.20
        assert row.total_position_cap_pct == 0.80
        assert row.max_new_positions_per_day == 3
        assert row.daily_loss_halt_pct == 0.05
        assert row.cooldown_days == 5
        assert row.initial_cash == 100_000.0
        assert row.updated_at is not None


def test_order_row_roundtrip_defaults():
    with _session() as session:
        session.add(OrderRow(as_of=dt.date(2026, 7, 17), symbol="AAPL", side="buy",
                             shares=10, status="pending_confirmation", mode="semi_auto"))
        session.commit()
        row = session.scalars(select(OrderRow)).one()
        assert row.id is not None and row.reason == ""
        assert row.decision_id is None
        assert row.created_at is not None and row.updated_at is not None


def test_paper_and_ops_rows_roundtrip():
    with _session() as session:
        session.add(PaperAccountRow(id=1, cash=100_000.0))
        session.add(PaperPositionRow(symbol="AAPL", shares=10, avg_cost=101.0))
        session.add(PaperFillRow(order_id=1, fill_date=dt.date(2026, 7, 20), symbol="AAPL",
                                 side="buy", shares=10, price=101.0))
        session.add(HeartbeatRow(job="premarket_screen", ok=True,
                                 ran_at=dt.datetime(2026, 7, 17, 12, 0)))
        session.add(AlertRow(kind="watchdog_downgrade", message="x"))
        session.commit()
        account = session.get(PaperAccountRow, 1)
        assert account.day_start_date is None
        assert account.day_start_equity is None
        assert account.breaker_tripped_on is None  # 熔断字段默认未触发
        assert session.scalars(select(PaperFillRow)).one().price == 101.0
        assert session.scalars(select(HeartbeatRow)).one().ok is True
        assert session.scalars(select(AlertRow)).one().created_at is not None
