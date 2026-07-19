import datetime as dt

import pytest

from app.risk.circuit_breaker import evaluate, is_tripped, should_trip
from app.store.db import init_db, make_engine, make_session_factory
from app.store.models import HeartbeatRow
from app.store.repos.paper_repo import get_account
from app.watchdog.monitor import MAX_GAP_HOURS, assess

D = dt.date(2026, 7, 17)
D1 = dt.date(2026, 7, 20)


@pytest.fixture
def engine():
    engine = make_engine(":memory:")
    init_db(engine)
    return engine


def test_should_trip_math():
    assert should_trip(equity=94_999.0, day_start_equity=100_000.0, daily_loss_halt_pct=0.05)
    assert not should_trip(equity=95_001.0, day_start_equity=100_000.0, daily_loss_halt_pct=0.05)
    assert not should_trip(equity=100.0, day_start_equity=0.0, daily_loss_halt_pct=0.05)


def test_negative_day_start_equity_never_trips():
    # 边界:day_start_equity 的 fail-safe 守卫是 `<= 0`,负数(脏数据/异常)也不应触发熔断判定
    assert should_trip(equity=-50.0, day_start_equity=-100.0, daily_loss_halt_pct=0.05) is False


def test_exact_gap_boundary_still_healthy():
    # 边界:心跳 gap 恰好等于 MAX_GAP_HOURS(未超过)→ 仍健康,钉住 assess 里的严格 `>`
    now = dt.datetime(2026, 7, 17, 12, 0)
    beat = HeartbeatRow(job="premarket_screen", ok=True,
                        ran_at=now - dt.timedelta(hours=MAX_GAP_HOURS))
    out = assess([beat], "premarket_screen", now)
    assert out.healthy is True


def test_evaluate_snapshots_day_start_then_trips(engine):
    with make_session_factory(engine)() as session:
        account = get_account(session, 100_000.0)
        assert evaluate(session, account, D, 100_000.0, 0.05) is False
        assert account.day_start_date == D
        assert account.day_start_equity == 100_000.0
        assert evaluate(session, account, D, 94_000.0, 0.05) is True  # 回撤 6% >= 5%
        assert account.breaker_tripped_on == D
        session.commit()


def test_tripped_state_survives_restart_same_day(engine):
    # 红线:熔断状态持久化,同日重启不重置;当日权益回升也不解除
    with make_session_factory(engine)() as session:
        account = get_account(session, 100_000.0)
        evaluate(session, account, D, 100_000.0, 0.05)
        evaluate(session, account, D, 90_000.0, 0.05)
        session.commit()
    with make_session_factory(engine)() as session:  # 模拟重启:同一 DB 新开 session
        account = get_account(session, 100_000.0)
        assert is_tripped(account, D) is True
        assert evaluate(session, account, D, 99_000.0, 0.05) is True


def test_next_day_resets(engine):
    with make_session_factory(engine)() as session:
        account = get_account(session, 100_000.0)
        evaluate(session, account, D, 100_000.0, 0.05)
        evaluate(session, account, D, 90_000.0, 0.05)
        assert evaluate(session, account, D1, 90_000.0, 0.05) is False  # 新一天新基线
        assert account.day_start_equity == 90_000.0
        assert is_tripped(account, D1) is False
