import datetime as dt

import pytest

from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.alert_repo import add_alert, get_alerts
from app.store.repos.heartbeat_repo import record_heartbeat, recent_heartbeats


@pytest.fixture
def session():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


def test_record_and_recent_heartbeats_desc_and_filtered(session):
    a = record_heartbeat(session, "premarket_screen", ok=True,
                         ran_at=dt.datetime(2026, 7, 17, 12, 0))
    b = record_heartbeat(session, "premarket_screen", ok=False,
                         ran_at=dt.datetime(2026, 7, 17, 13, 0), detail="boom")
    record_heartbeat(session, "other_job", ok=True, ran_at=dt.datetime(2026, 7, 17, 14, 0))
    beats = recent_heartbeats(session, "premarket_screen")
    assert [x.id for x in beats] == [b.id, a.id]  # 新→旧
    assert beats[0].detail == "boom" and beats[0].ok is False


def test_recent_heartbeats_limit(session):
    for hour in range(5):
        record_heartbeat(session, "j", ok=True, ran_at=dt.datetime(2026, 7, 17, hour))
    assert len(recent_heartbeats(session, "j", limit=3)) == 3


def test_alerts_roundtrip(session):
    add_alert(session, "watchdog_downgrade", "mode full_auto -> advisory")
    add_alert(session, "other", "x")
    assert [a.kind for a in get_alerts(session)] == ["watchdog_downgrade", "other"]
    assert len(get_alerts(session, "other")) == 1
