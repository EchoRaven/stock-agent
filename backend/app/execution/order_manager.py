"""订单生命周期 + 模式分流:全系统唯一的下单 choke point。

安全红线:
- mode 由 decision_service 从 DB 读出后传入,本模块绝不采信 payload;
- 任何订单(semi/full、创建/批准)必须先过 RiskGate,拒绝即不提交,且留审计单;
- 未知模式 fail-safe 不建单;
- 闸门/查重/下单用的 as_of 由调用方显式传入(服务端时钟派生),绝不采信
  decision.as_of——decision.as_of 来自 payload,可被伪造成未来日期以绕过
  熔断/冷却/查重(M3 final review 确认漏洞);decision.as_of 只保留作审计字段。
"""
import datetime as dt
import logging

from sqlalchemy.orm import Session

from app.execution.account_state import build_account_state
from app.execution.base import Broker
from app.execution.paper import PaperBroker
from app.risk.gate import RiskGate, params_from_row
from app.risk.rules import OrderRequest
from app.store.models import DecisionRow, OrderRow
from app.store.repos import order_repo
from app.store.repos.settings_repo import (MODE_FULL_AUTO, MODE_SEMI_AUTO, get_app_settings,
                                           get_execution_backend)

logger = logging.getLogger(__name__)

_gate = RiskGate()


def _get_broker(session: Session) -> Broker:
    """执行后端工厂:按 settings.execution_backend 挑 broker 实例。

    安全红线:唯一可达分支是 get_execution_backend() 的返回值,而它只可能是
    settings_repo.EXECUTION_BACKENDS 里的 "paper"/"futu_paper"(set_execution_backend
    拒绝其余一切值)——这里没有、也永远不会有能触达真实资金的分支。REAL 交易
    完全在 FutuBroker 内部靠 env-only 的 futu_allow_real + futu_unlock_pwd 硬门控,
    与这个开关无关。FutuBroker 惰性 import,保证未装 futu-api 时本模块仍可正常
    import(见 tests/execution/test_order_manager.py 的 import 守卫)。
    """
    backend = get_execution_backend(session)
    if backend == "futu_paper":
        from app.execution.futu_broker import FutuBroker
        return FutuBroker()
    return PaperBroker()


def order_to_dict(row: OrderRow) -> dict:
    return {"id": row.id, "as_of": row.as_of.isoformat(), "symbol": row.symbol,
            "side": row.side, "shares": row.shares, "status": row.status,
            "mode": row.mode, "reason": row.reason, "decision_id": row.decision_id}


def _gate_check(session: Session, symbol: str, side: str, shares: int,
                as_of: dt.date, prices: dict):
    request = OrderRequest(symbol=symbol, side=side, shares=shares,
                           price=float(prices.get(symbol, 0.0)), as_of=as_of)
    account = build_account_state(session, as_of, prices)
    return _gate.check(request, account, params_from_row(get_app_settings(session)))


def handle_decision(session: Session, decision: DecisionRow, mode: str,
                    shares: int, prices: dict, *, as_of: dt.date) -> dict:
    """semi_auto → 待确认队列;full_auto → 过闸门后直提 PaperBroker;其余不建单。

    as_of 必须由调用方显式传入(server-derived gating date)——绝不用
    decision.as_of(payload 可伪造未来日期绕过熔断/冷却/查重)。
    """
    if mode not in (MODE_SEMI_AUTO, MODE_FULL_AUTO):
        return {"order": None, "note": f"mode {mode!r} does not create orders"}
    symbol, side = decision.symbol, decision.action
    if order_repo.has_active_order(session, as_of, symbol, side):
        logger.warning("duplicate order suppressed for %s %s on %s", side, symbol, as_of)
        return {"order": None,
                "note": f"duplicate protection: active {side} order already exists "
                        f"for {symbol} on {as_of}"}
    check = _gate_check(session, symbol, side, shares, as_of, prices)
    if not check.allowed:
        row = order_repo.create_order(session, as_of, symbol, side, shares,
                                      order_repo.STATUS_REJECTED, mode,
                                      decision_id=decision.id, reason=check.reason)
        return {"order": order_to_dict(row), "note": "rejected by risk gate"}
    if mode == MODE_SEMI_AUTO:
        row = order_repo.create_order(session, as_of, symbol, side, shares,
                                      order_repo.STATUS_PENDING_CONFIRMATION, mode,
                                      decision_id=decision.id)
        return {"order": order_to_dict(row), "note": "queued for confirmation"}
    row = order_repo.create_order(session, as_of, symbol, side, shares,
                                  order_repo.STATUS_APPROVED, mode,
                                  decision_id=decision.id)
    row = _get_broker(session).submit(session, row)
    return {"order": order_to_dict(row), "note": "submitted to paper broker"}


def list_pending(session: Session) -> list:
    return [order_to_dict(r) for r in
            order_repo.get_orders_by_status(session, order_repo.STATUS_PENDING_CONFIRMATION)]


def approve_order(session: Session, order_id: int, as_of: dt.date, prices: dict) -> dict:
    """人工批准。批准时刻重新过闸门(不是只在创建时)——市场/参数可能已变。"""
    row = order_repo.get_order(session, order_id)
    if row is None or row.status != order_repo.STATUS_PENDING_CONFIRMATION:
        return {"order": order_to_dict(row) if row else None,
                "note": f"order {order_id} is not pending confirmation"}
    check = _gate_check(session, row.symbol, row.side, row.shares, as_of, prices)
    if not check.allowed:
        row = order_repo.update_status(session, order_id, order_repo.STATUS_REJECTED,
                                       reason=f"rejected at approval: {check.reason}")
        return {"order": order_to_dict(row), "note": "rejected by risk gate at approval"}
    row = order_repo.update_status(session, order_id, order_repo.STATUS_APPROVED)
    row = _get_broker(session).submit(session, row)
    return {"order": order_to_dict(row), "note": "approved and submitted"}


def reject_order(session: Session, order_id: int, reason: str = "rejected by user") -> dict:
    row = order_repo.get_order(session, order_id)
    if row is None or row.status != order_repo.STATUS_PENDING_CONFIRMATION:
        return {"order": order_to_dict(row) if row else None,
                "note": f"order {order_id} is not pending confirmation"}
    row = order_repo.update_status(session, order_id, order_repo.STATUS_REJECTED,
                                   reason=reason)
    return {"order": order_to_dict(row), "note": "rejected"}


def settle_open(session: Session, fill_date: dt.date, open_prices: dict) -> list:
    """下一交易时段开盘撮合(CLI/cron 触发)。"""
    return [
        {"order_id": f.order_id, "symbol": f.symbol, "side": f.side,
         "shares": f.shares, "price": round(f.price, 4),
         "fill_date": f.fill_date.isoformat()}
        for f in _get_broker(session).process_fills(session, fill_date, open_prices)
    ]
