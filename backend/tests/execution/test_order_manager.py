import datetime as dt

import pytest

from app.execution.order_manager import (approve_order, handle_decision, list_pending,
                                         order_to_dict, reject_order, settle_open)
from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.decision_repo import save_decision
from app.store.repos.order_repo import (STATUS_FILLED, STATUS_PENDING_CONFIRMATION,
                                        STATUS_REJECTED, STATUS_SUBMITTED, get_order)
from app.store.repos.paper_repo import get_account, get_positions
from app.store.repos.settings_repo import (MODE_FULL_AUTO, MODE_SEMI_AUTO,
                                           update_risk_params)

D = dt.date(2026, 7, 17)
D1 = dt.date(2026, 7, 20)
PRICES = {"AAPL": 100.0}


@pytest.fixture
def session():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


def _decision(session, symbol="AAPL", action="buy"):
    return save_decision(session, D, symbol, action, 0.8, "semi_auto", "{}")


def test_semi_auto_queues_pending(session):
    out = handle_decision(session, _decision(session), MODE_SEMI_AUTO, 10, PRICES, as_of=D)
    assert out["order"]["status"] == STATUS_PENDING_CONFIRMATION
    assert out["order"]["decision_id"] is not None
    assert [o["id"] for o in list_pending(session)] == [out["order"]["id"]]


def test_full_auto_within_caps_submits(session):
    out = handle_decision(session, _decision(session), MODE_FULL_AUTO, 10, PRICES, as_of=D)
    assert out["order"]["status"] == STATUS_SUBMITTED


def test_full_auto_over_cap_rejected_not_submitted(session):
    # 红线:full_auto 超单票上限(20% × 10 万 = 2 万)必须被闸门拒绝,不提交 broker
    out = handle_decision(session, _decision(session), MODE_FULL_AUTO, 300, PRICES, as_of=D)
    assert out["order"]["status"] == STATUS_REJECTED
    assert "single-position cap" in out["order"]["reason"]
    assert get_positions(session) == {}
    assert get_account(session, 100_000.0).cash == 100_000.0
    assert settle_open(session, D1, PRICES) == []  # 没有任何已提交订单可撮合


def test_semi_auto_over_cap_rejected_at_creation(session):
    out = handle_decision(session, _decision(session), MODE_SEMI_AUTO, 300, PRICES, as_of=D)
    assert out["order"]["status"] == STATUS_REJECTED
    assert list_pending(session) == []


def test_duplicate_active_order_suppressed(session):
    handle_decision(session, _decision(session), MODE_SEMI_AUTO, 10, PRICES, as_of=D)
    out = handle_decision(session, _decision(session), MODE_SEMI_AUTO, 10, PRICES, as_of=D)
    assert out["order"] is None and "duplicate" in out["note"]
    assert len(list_pending(session)) == 1


def test_sell_not_blocked_by_active_buy_same_symbol(session):
    # fix 2:重复保护按 (as_of, symbol, side) 隔离——同日同标的的活跃 buy
    # 不应挡住 sell(风险降低型平仓不是重复下单)
    handle_decision(session, _decision(session, action="buy"), MODE_SEMI_AUTO, 10,
                    PRICES, as_of=D)
    out = handle_decision(session, _decision(session, action="sell"), MODE_SEMI_AUTO, 5,
                          PRICES, as_of=D)
    assert out["order"] is not None
    assert out["order"]["status"] == STATUS_PENDING_CONFIRMATION
    assert out["order"]["side"] == "sell"
    assert len(list_pending(session)) == 2


def test_unknown_mode_creates_nothing(session):
    # fail-safe:未知模式绝不建单
    out = handle_decision(session, _decision(session), "yolo", 10, PRICES, as_of=D)
    assert out["order"] is None
    assert list_pending(session) == []


def test_approve_regates_with_fresh_params(session):
    # 红线:批准时刻重新过闸门——创建时合法,批准前收紧参数后必须拒绝
    out = handle_decision(session, _decision(session), MODE_SEMI_AUTO, 10, PRICES, as_of=D)
    update_risk_params(session, single_position_cap_pct=0.001)  # 上限收紧到 100 元
    approved = approve_order(session, out["order"]["id"], D, PRICES)
    assert approved["order"]["status"] == STATUS_REJECTED
    assert "rejected at approval" in approved["order"]["reason"]


def test_approve_then_settle_fills(session):
    out = handle_decision(session, _decision(session), MODE_SEMI_AUTO, 10, PRICES, as_of=D)
    approved = approve_order(session, out["order"]["id"], D, PRICES)
    assert approved["order"]["status"] == STATUS_SUBMITTED
    fills = settle_open(session, D1, {"AAPL": 100.0})
    assert len(fills) == 1 and fills[0]["shares"] == 10
    assert get_order(session, out["order"]["id"]).status == STATUS_FILLED


def test_reject_order(session):
    out = handle_decision(session, _decision(session), MODE_SEMI_AUTO, 10, PRICES, as_of=D)
    rejected = reject_order(session, out["order"]["id"], reason="不想买")
    assert rejected["order"]["status"] == STATUS_REJECTED
    assert rejected["order"]["reason"] == "不想买"
    assert list_pending(session) == []


def test_approve_nonpending_is_refused(session):
    out = handle_decision(session, _decision(session), MODE_FULL_AUTO, 10, PRICES, as_of=D)
    result = approve_order(session, out["order"]["id"], D, PRICES)
    assert result["order"]["status"] == STATUS_SUBMITTED  # 原样返回,不重复提交
    assert "not pending" in result["note"]
    assert order_to_dict(get_order(session, out["order"]["id"]))["status"] == STATUS_SUBMITTED
