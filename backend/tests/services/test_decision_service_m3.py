"""M3:mode 唯一真相在 DB;payload 指定 mode/旁路字段一律无效;按模式分流。"""
import datetime as dt

import pytest

from app.services.decision_service import (DecisionValidationError, submit_decision,
                                           validate_decision)
from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.order_repo import (STATUS_PENDING_CONFIRMATION, STATUS_REJECTED,
                                        STATUS_SUBMITTED, get_orders_by_status)
from app.store.repos.paper_repo import set_position
from app.store.repos.settings_repo import (MODE_FULL_AUTO, MODE_SEMI_AUTO,
                                           get_app_settings, set_mode)
from tests.helpers import make_decision_payload

PRICES = {"AAPL": 100.0}
# 闸门 as_of 现由服务端时钟(now_utc)派生,不再采信 payload.as_of;固定注入以保持
# 用例确定性,并映射到 payload 默认 as_of("2026-07-17")所在的 ET 交易日。
NOW_UTC = dt.datetime(2026, 7, 17, 16, 0, tzinfo=dt.UTC)


@pytest.fixture
def session():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


def _all_orders(session):
    return [row for status in (STATUS_PENDING_CONFIRMATION, STATUS_SUBMITTED,
                               STATUS_REJECTED)
            for row in get_orders_by_status(session, status)]


def test_unset_mode_fail_safe_advisory_no_order(session):
    # 红线:未设 → advisory,不生成订单
    result = submit_decision(session, make_decision_payload(), prices=PRICES)
    assert result["status"] == "recorded" and result["mode"] == "advisory"
    assert _all_orders(session) == []


def test_payload_cannot_force_mode(session):
    # 红线:mode 只从 DB 读;payload 传 full_auto + 旁路字段一律无效
    payload = make_decision_payload(mode="full_auto", risk_override=True, skip_gate=True)
    result = submit_decision(session, payload, prices=PRICES)
    assert result["mode"] == "advisory"
    assert _all_orders(session) == []


def test_unknown_db_mode_fail_safe_advisory(session):
    # 红线:DB 值被写坏 → advisory
    get_app_settings(session).mode = "yolo"
    session.flush()
    result = submit_decision(session, make_decision_payload(), prices=PRICES)
    assert result["mode"] == "advisory"
    assert _all_orders(session) == []


def test_semi_auto_creates_pending_order(session):
    set_mode(session, MODE_SEMI_AUTO)
    result = submit_decision(session, make_decision_payload(), prices=PRICES, now_utc=NOW_UTC)
    assert result["mode"] == MODE_SEMI_AUTO
    assert result["order"]["status"] == STATUS_PENDING_CONFIRMATION


def test_full_auto_within_caps_submits(session):
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    result = submit_decision(session, make_decision_payload(), prices=PRICES, now_utc=NOW_UTC)
    assert result["order"]["status"] == STATUS_SUBMITTED


def test_full_auto_over_cap_rejected_even_with_bypass_keys(session):
    # 红线:gate 不可被 payload/工具参数绕过
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    payload = make_decision_payload(shares=300, skip_gate=True, risk_override="all")
    result = submit_decision(session, payload, prices=PRICES, now_utc=NOW_UTC)
    assert result["order"]["status"] == STATUS_REJECTED
    assert "single-position cap" in result["order"]["reason"]


def test_full_auto_buy_without_price_fail_safe_rejected(session):
    # 服务端取不到参考价 → default-deny,而不是按 0 元放行
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    result = submit_decision(session, make_decision_payload(), prices={}, now_utc=NOW_UTC)
    assert result["order"]["status"] == STATUS_REJECTED


def test_stale_held_quote_blocks_buys_allows_sells(session):
    # 红线加固(finding #6):某持仓当前报价缺失 → 权益不可信 → full_auto 下
    # 任何标的的买单被拒(仅允许卖出)。删掉 StaleQuoteRule 或 account_state 的
    # stale 采集,此测试即 fail。
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    set_position(session, "AAPL", 10, 100.0)  # 持有 AAPL
    # 买 MSFT,但 prices 里没有持仓 AAPL 的报价 → AAPL stale → 买单一律拒
    buy = submit_decision(session, make_decision_payload(symbol="MSFT", shares=1),
                          prices={"MSFT": 50.0}, now_utc=NOW_UTC)
    assert buy["order"]["status"] == STATUS_REJECTED
    assert "报价缺失" in buy["order"]["reason"]
    # 卖出持仓仍放行(AAPL 报价依旧缺失)
    sell = submit_decision(session, make_decision_payload(symbol="AAPL", action="sell",
                                                         shares=5), prices={"MSFT": 50.0},
                           now_utc=NOW_UTC)
    assert sell["order"]["status"] == STATUS_SUBMITTED


def test_hold_never_creates_order(session):
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    payload = make_decision_payload(action="hold")
    del payload["shares"]  # hold 不要求 shares
    result = submit_decision(session, payload, prices=PRICES)
    assert result["status"] == "recorded"
    assert _all_orders(session) == []


def test_shares_required_for_trade_actions():
    payload = make_decision_payload()
    del payload["shares"]
    with pytest.raises(DecisionValidationError):
        validate_decision(payload)
    with pytest.raises(DecisionValidationError):
        validate_decision(make_decision_payload(shares=0))
    with pytest.raises(DecisionValidationError):
        validate_decision(make_decision_payload(shares=True))
