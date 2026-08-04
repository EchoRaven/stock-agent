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


# ---------------------------------------------------------------------------
# Finding A 遏制性回归(money-path review):submit_decision 目前**信任调用方传入
# 的 shares**(不在函数内服务端重算),只由闸门/撮合把它兜住。这不是当前可利用
# 的漏洞,但字面红线#2("shares 永远服务端算")靠的是纵深防御而非直接强制。以下
# 两个用例把"即便传天量 shares 也无害"这一遏制性质钉成可执行不变量——若将来闸门
# 或撮合的边界被削弱导致超限建仓 / 超卖,这两个测试立即 fail(正是 review 担心的
# 未来回归点)。
# ---------------------------------------------------------------------------

def test_finding_a_oversized_buy_shares_cannot_exceed_single_position_cap(session):
    """天量买入 shares 也绝不可能建成超过单标的上限的仓位——闸门按 服务端价 ×
    shares 判定并直接拒单,不产生任何持仓。"""
    from app.store.repos.paper_repo import get_positions
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    # equity=initial_cash(100k),单标的上限默认 0.20 → 20k;price 100 → 服务端上限 200 股
    payload = make_decision_payload(symbol="AAPL", shares=10_000_000)
    result = submit_decision(session, payload, prices={"AAPL": 100.0}, now_utc=NOW_UTC)
    assert result["order"]["status"] == STATUS_REJECTED
    assert "single-position cap" in result["order"]["reason"]
    assert get_positions(session) == {}  # 没有任何超限仓位落地


def test_finding_a_oversized_sell_shares_cannot_oversell(session):
    """天量卖出 shares 撮合时只会卖出实际持有的股数(PaperBroker._execute 取
    min(order.shares, held)),持仓归零、绝不出现负仓/超卖。"""
    from app.execution.order_manager import settle_open
    from app.store.repos.paper_repo import get_positions
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    set_position(session, "AAPL", 10, 90.0)  # 实际只持有 10 股
    payload = make_decision_payload(symbol="AAPL", action="sell", shares=10_000_000)
    result = submit_decision(session, payload, prices={"AAPL": 100.0}, now_utc=NOW_UTC)
    assert result["order"]["status"] == STATUS_SUBMITTED  # 卖出放行(闸门不拦减仓)

    fills = settle_open(session, dt.date(2026, 7, 17), {"AAPL": 100.0})
    session.commit()
    assert len(fills) == 1
    assert fills[0]["shares"] == 10  # 只卖实际持有的 10 股,不是 10_000_000
    assert "AAPL" not in get_positions(session)  # 归零删仓,无负仓


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
