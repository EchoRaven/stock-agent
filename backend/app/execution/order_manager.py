"""订单生命周期 + 模式分流:全系统唯一的下单 choke point。

安全红线:
- mode 由 decision_service 从 DB 读出后传入,本模块绝不采信 payload;
- 任何订单(semi/full、创建/批准)必须先过 RiskGate,拒绝即不提交,且留审计单;
- 未知模式 fail-safe 不建单。
"""
import datetime as dt
import logging

from sqlalchemy.orm import Session

from app.execution.account_state import build_account_state
from app.execution.paper import PaperBroker
from app.risk.gate import RiskGate, params_from_row
from app.risk.rules import OrderRequest
from app.store.models import DecisionRow, OrderRow
from app.store.repos import order_repo
from app.store.repos.settings_repo import MODE_FULL_AUTO, MODE_SEMI_AUTO, get_app_settings

logger = logging.getLogger(__name__)

_gate = RiskGate()
_broker = PaperBroker()


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
                    shares: int, prices: dict) -> dict:
    """semi_auto → 待确认队列;full_auto → 过闸门后直提 PaperBroker;其余不建单。"""
    if mode not in (MODE_SEMI_AUTO, MODE_FULL_AUTO):
        return {"order": None, "note": f"mode {mode!r} does not create orders"}
    as_of, symbol, side = decision.as_of, decision.symbol, decision.action
    if order_repo.has_active_order(session, as_of, symbol):
        logger.warning("duplicate order suppressed for %s on %s", symbol, as_of)
        return {"order": None,
                "note": f"duplicate protection: active order already exists "
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
    row = _broker.submit(session, row)
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
    row = _broker.submit(session, row)
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
        for f in _broker.process_fills(session, fill_date, open_prices)
    ]
