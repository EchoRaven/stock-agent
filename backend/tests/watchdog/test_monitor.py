import datetime as dt

import pytest

from app.store.db import init_db, make_engine, make_session_factory
from app.store.models import HeartbeatRow
from app.store.repos.alert_repo import get_alerts
from app.store.repos.heartbeat_repo import record_heartbeat
from app.store.repos.settings_repo import (MODE_FULL_AUTO, MODE_SEMI_AUTO, get_mode,
                                           set_mode)
from app.watchdog.monitor import Verdict, assess, check_and_enforce

NOW = dt.datetime(2026, 7, 17, 12, 0)  # naive-UTC,注入


def _hb(hours_ago: float, ok: bool = True):
    return HeartbeatRow(job="premarket_screen", ok=ok,
                        ran_at=NOW - dt.timedelta(hours=hours_ago))


def test_assess_no_heartbeats_unhealthy():
    assert assess([], "premarket_screen", NOW) == Verdict(
        False, "premarket_screen: no heartbeat recorded")


def test_assess_fresh_ok_healthy():
    assert assess([_hb(2.0)], "premarket_screen", NOW).healthy


def test_assess_stale_unhealthy():
    out = assess([_hb(31.0)], "premarket_screen", NOW)
    assert not out.healthy and "31.0h" in out.reason


def test_assess_consecutive_failures_unhealthy():
    out = assess([_hb(1.0, ok=False), _hb(2.0, ok=False), _hb(3.0)],
                 "premarket_screen", NOW)
    assert not out.healthy and "consecutive failures" in out.reason


def test_assess_single_failure_then_success_healthy():
    assert assess([_hb(1.0, ok=False), _hb(2.0)], "premarket_screen", NOW).healthy


@pytest.fixture
def session():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


def test_downgrades_full_auto_and_records_alert(session):
    # 红线:watchdog 检测异常自动降级 advisory + 记 alert
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    session.commit()
    out = check_and_enforce(session, NOW)  # 无任何心跳 → unhealthy
    assert out["downgraded"] is True and out["mode_after"] == "advisory"
    assert get_mode(session) == "advisory"
    alerts = get_alerts(session, "watchdog_downgrade")
    assert len(alerts) == 1 and "full_auto" in alerts[0].message


def test_semi_auto_also_downgrades(session):
    set_mode(session, MODE_SEMI_AUTO)
    session.commit()
    assert check_and_enforce(session, NOW)["mode_after"] == "advisory"


def test_healthy_heartbeat_keeps_mode(session):
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    record_heartbeat(session, "premarket_screen", ok=True,
                     ran_at=NOW - dt.timedelta(hours=1))
    session.commit()
    out = check_and_enforce(session, NOW)
    assert out["healthy"] is True and out["downgraded"] is False
    assert get_mode(session) == "full_auto"


def test_advisory_stays_without_alert(session):
    out = check_and_enforce(session, NOW)
    assert out["downgraded"] is False and out["mode_after"] == "advisory"
    assert get_alerts(session) == []
