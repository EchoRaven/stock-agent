"""M3 全链路:半自动与全自动在模拟盘上的闭环(fake 注入,离线)。"""
import datetime as dt

import pytest

from app.execution.order_manager import approve_order, list_pending, settle_open
from app.services.decision_service import submit_decision
from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.order_repo import (STATUS_FILLED, STATUS_PENDING_CONFIRMATION,
                                        STATUS_REJECTED, STATUS_SUBMITTED, get_order)
from app.store.repos.paper_repo import get_account, get_positions
from app.store.repos.settings_repo import MODE_FULL_AUTO, MODE_SEMI_AUTO, set_mode
from tests.helpers import make_decision_payload

D = dt.date(2026, 7, 17)
D1 = dt.date(2026, 7, 20)


@pytest.fixture
def session():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


def test_semi_auto_closed_loop(session):
    # 决定 → 待确认 → 人工批准(重过闸)→ 提交 → 次一交易时段开盘成交
    set_mode(session, MODE_SEMI_AUTO)
    result = submit_decision(session, make_decision_payload(), prices={"AAPL": 100.0})
    assert result["order"]["status"] == STATUS_PENDING_CONFIRMATION
    assert [o["id"] for o in list_pending(session)] == [result["order"]["id"]]
    approved = approve_order(session, result["order"]["id"], D, {"AAPL": 100.0})
    assert approved["order"]["status"] == STATUS_SUBMITTED
    fills = settle_open(session, D1, {"AAPL": 101.0})
    session.commit()
    assert len(fills) == 1 and fills[0]["shares"] == 10
    assert get_order(session, result["order"]["id"]).status == STATUS_FILLED
    assert get_positions(session)["AAPL"].shares == 10
    assert get_account(session, 100_000.0).cash < 100_000.0


def test_full_auto_closed_loop(session):
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    result = submit_decision(session, make_decision_payload(), prices={"AAPL": 100.0})
    assert result["order"]["status"] == STATUS_SUBMITTED
    fills = settle_open(session, D1, {"AAPL": 100.0})
    session.commit()
    assert len(fills) == 1
    assert get_positions(session)["AAPL"].shares == 10


def test_breaker_day_full_auto_only_sells(session):
    # 红线集成:日内权益回撤 >= 5% 触发熔断后,full_auto 当日买单全拒、卖单放行
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    submit_decision(session, make_decision_payload(shares=150), prices={"AAPL": 100.0})
    settle_open(session, D1, {"AAPL": 100.0})  # 持仓 150 股,成本约 100.05
    # D1 第一次评估(正常价):day_start 快照约 99_992.5
    first = submit_decision(session, make_decision_payload(
        symbol="MSFT", as_of="2026-07-20", shares=1),
        prices={"AAPL": 100.0, "MSFT": 10.0})
    assert first["order"]["status"] == STATUS_SUBMITTED
    # AAPL 暴跌至 50:权益 ~92_492,回撤 ~7.5% → 熔断,买单拒
    crashed = submit_decision(session, make_decision_payload(
        symbol="NVDA", as_of="2026-07-20", shares=1),
        prices={"AAPL": 50.0, "NVDA": 10.0})
    assert crashed["order"]["status"] == STATUS_REJECTED
    assert "circuit breaker" in crashed["order"]["reason"]
    assert get_account(session, 100_000.0).breaker_tripped_on == D1
    # 熔断日卖出放行
    sell = submit_decision(session, make_decision_payload(
        action="sell", as_of="2026-07-20", shares=150),
        prices={"AAPL": 50.0})
    assert sell["order"]["status"] == STATUS_SUBMITTED
