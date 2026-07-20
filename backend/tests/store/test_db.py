import datetime as dt

from sqlalchemy import inspect, select, text

from app.store.db import init_db, make_engine, make_session_factory
from app.store.models import DecisionRow, ReportRow, SignalRow
from app.store.repos.settings_repo import get_execution_backend


def test_init_db_creates_tables():
    engine = make_engine(":memory:")
    init_db(engine)
    assert {"signals", "decisions", "reports"} <= set(inspect(engine).get_table_names())


def test_execution_backend_column_added_to_legacy_settings_table(tmp_path):
    """老库模拟:先按缺 execution_backend 列的 schema 建 settings 表(镜像
    M4 合并前的真实本地 DB),init_db 必须原地补列而不是炸掉或丢数据。"""
    engine = make_engine(tmp_path / "legacy.db")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE settings (id INTEGER PRIMARY KEY, mode VARCHAR(16), "
            "single_position_cap_pct FLOAT, total_position_cap_pct FLOAT, "
            "max_new_positions_per_day INTEGER, daily_loss_halt_pct FLOAT, "
            "cooldown_days INTEGER, initial_cash FLOAT, updated_at DATETIME)"))
        conn.execute(text("INSERT INTO settings (id, mode) VALUES (1, 'advisory')"))

    init_db(engine)  # must not raise

    cols = {row[1] for row in engine.connect().exec_driver_sql("PRAGMA table_info(settings)")}
    assert "execution_backend" in cols
    with make_session_factory(engine)() as session:
        # ALTER ... DEFAULT 'paper' backfills the existing row automatically.
        assert get_execution_backend(session) == "paper"

    init_db(engine)  # idempotent: running again on an already-migrated DB is a no-op, not an error


def test_init_db_idempotent_on_fresh_db():
    engine = make_engine(":memory:")
    init_db(engine)
    init_db(engine)  # must not raise
    cols = {row[1] for row in engine.connect().exec_driver_sql("PRAGMA table_info(settings)")}
    assert "execution_backend" in cols


def test_roundtrip_rows():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as session:
        session.add(SignalRow(as_of=dt.date(2026, 7, 17), symbol="AAPL",
                              rank=1, total=0.9, parts_json="{}"))
        session.add(DecisionRow(as_of=dt.date(2026, 7, 17), symbol="AAPL", action="buy",
                                confidence=0.8, mode="advisory", payload_json="{}"))
        session.add(ReportRow(report_date=dt.date(2026, 7, 17), kind="daily", content_md="# hi"))
        session.commit()
        assert session.scalars(select(SignalRow)).one().symbol == "AAPL"
        row = session.scalars(select(DecisionRow)).one()
        assert row.mode == "advisory" and row.created_at is not None
        assert session.scalars(select(ReportRow)).one().content_md == "# hi"


def test_file_engine_creates_parent_dir(tmp_path):
    engine = make_engine(tmp_path / "nested" / "app.db")
    init_db(engine)
    assert (tmp_path / "nested" / "app.db").exists()
