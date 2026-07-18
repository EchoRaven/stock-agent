import datetime as dt

from sqlalchemy import inspect, select

from app.store.db import init_db, make_engine, make_session_factory
from app.store.models import DecisionRow, ReportRow, SignalRow


def test_init_db_creates_tables():
    engine = make_engine(":memory:")
    init_db(engine)
    assert {"signals", "decisions", "reports"} <= set(inspect(engine).get_table_names())


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
