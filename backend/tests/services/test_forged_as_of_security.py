"""M3 final review 回归测试:payload.as_of 伪造成未来日期不得绕过熔断/冷却。

CONFIRMED 漏洞(评审现场探测):旧代码 handle_decision 用 decision.as_of(来自
payload)做闸门/查重的 as_of。把 payload.as_of 伪造成未来日期后,
circuit_breaker.is_tripped(account, as_of) 与 CooldownRule 都会拿伪造日期去比对,
真实交易日的熔断/冷却状态形同虚设——买单被放行并真的成交。

修复后 gate_as_of 由服务端时钟(now_utc)经 et_trading_day 派生,payload.as_of
只落入审计记录,不再进入任何闸门判定。

复现方法(见本文件顶部注释,不作为自动化用例执行):把
decision_service.submit_decision 里的
    gate_as_of = et_trading_day(now_utc or dt.datetime.now(dt.UTC))
临时改回旧漏洞的等价写法
    gate_as_of = dt.date.fromisoformat(normalized["as_of"])
后单独跑本文件,两个 forged 用例会失败(订单被放行/成交,而不是被拒绝)——
这就是修复前的 RED 状态;改回来即恢复 GREEN。
"""
import datetime as dt

import pytest

from app.execution.order_manager import settle_open
from app.services.decision_service import submit_decision
from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.order_repo import STATUS_REJECTED, STATUS_SUBMITTED
from app.store.repos.paper_repo import get_account, get_positions, set_position
from app.store.repos.settings_repo import MODE_FULL_AUTO, set_mode
from tests.helpers import make_decision_payload

D = dt.date(2026, 7, 17)
D1 = dt.date(2026, 7, 20)
FORGED_FAR_FUTURE = "2026-08-19"  # D1 + 30 天


def _utc_noon_et(day: dt.date) -> dt.datetime:
    """给定 ET 交易日,返回一个安全落在该日内的 UTC 时刻(夏令时 UTC-4,取 UTC 16:00)。"""
    return dt.datetime(day.year, day.month, day.day, 16, 0, tzinfo=dt.UTC)


NOW_UTC_D = _utc_noon_et(D)
NOW_UTC_D1 = _utc_noon_et(D1)


@pytest.fixture
def session():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


def test_forged_future_as_of_cannot_untrip_breaker(session):
    # 1) 建仓,次日开盘成交
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    submit_decision(session, make_decision_payload(shares=150), prices={"AAPL": 100.0},
                    now_utc=NOW_UTC_D)
    settle_open(session, D1, {"AAPL": 100.0})  # 持仓 150 股,成本约 100.05

    # 2) D1 第一次评估(正常价)记录 day_start 快照;随后 AAPL 暴跌触发熔断
    first = submit_decision(session, make_decision_payload(
        symbol="MSFT", as_of="2026-07-20", shares=1),
        prices={"AAPL": 100.0, "MSFT": 10.0}, now_utc=NOW_UTC_D1)
    assert first["order"]["status"] == STATUS_SUBMITTED
    crashed = submit_decision(session, make_decision_payload(
        symbol="NVDA", as_of="2026-07-20", shares=1),
        prices={"AAPL": 50.0, "NVDA": 10.0}, now_utc=NOW_UTC_D1)
    assert crashed["order"]["status"] == STATUS_REJECTED
    assert "circuit breaker" in crashed["order"]["reason"]
    assert get_account(session, 100_000.0).breaker_tripped_on == D1

    # 3) 攻击:payload.as_of 伪造成 D1+30(未来),但服务端时钟(now_utc)仍是 D1——
    #    真实熔断当日。full_auto 买单必须仍被拒,且从未真正提交/成交。
    forged = submit_decision(session, make_decision_payload(
        symbol="TSLA", as_of=FORGED_FAR_FUTURE, shares=1),
        prices={"AAPL": 50.0, "TSLA": 10.0}, now_utc=NOW_UTC_D1)
    assert forged["order"]["status"] == STATUS_REJECTED, (
        "SECURITY REGRESSION: forged future as_of bypassed the tripped circuit breaker")
    assert "circuit breaker" in forged["order"]["reason"]
    assert "TSLA" not in get_positions(session)
    # 没有任何 TSLA 的已提交订单可撮合——确认伪造未导致真实成交
    assert settle_open(session, D1 + dt.timedelta(days=3), {"TSLA": 10.0}) == []


def test_forged_as_of_cannot_bypass_cooldown(session):
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    set_position(session, "AAA", 10, 50.0)

    # 卖出并在 D1 开盘成交,记录 last_sell_dates["AAA"] = D1
    sell = submit_decision(session, make_decision_payload(
        symbol="AAA", action="sell", shares=10), prices={"AAA": 50.0}, now_utc=NOW_UTC_D)
    assert sell["order"]["status"] == STATUS_SUBMITTED
    settle_open(session, D1, {"AAA": 50.0})

    # cooldown_days 默认 5;D1+4 仍在冷却期内(< 5 天)——真实服务端时钟下应被拒
    gate_day = D1 + dt.timedelta(days=4)
    forged_payload_as_of = (D1 + dt.timedelta(days=400)).isoformat()  # payload 伪造成远期未来
    rebuy = submit_decision(session, make_decision_payload(
        symbol="AAA", shares=1, as_of=forged_payload_as_of),
        prices={"AAA": 50.0}, now_utc=_utc_noon_et(gate_day))
    assert rebuy["order"]["status"] == STATUS_REJECTED, (
        "SECURITY REGRESSION: forged future as_of bypassed the cooldown rule")
    assert "cooldown" in rebuy["order"]["reason"]
