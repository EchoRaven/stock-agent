"""orders 仓储。重复保护:同 (as_of, symbol, side) 只允许一张活跃订单(防重复下单)。

side 隔离:同日同标的的反向操作(如已有活跃 buy 时的 sell)不算重复——
sell 通常是风险降低型平仓,不应被同标的的活跃 buy 挡住。
rejected/cancelled 是终态审计记录,不占用重复保护槽位——拒绝必须留痕。
"""
import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.store.models import OrderRow

STATUS_PENDING_CONFIRMATION = "pending_confirmation"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_SUBMITTED = "submitted"
STATUS_FILLED = "filled"
STATUS_CANCELLED = "cancelled"
STATUSES = (STATUS_PENDING_CONFIRMATION, STATUS_APPROVED, STATUS_REJECTED,
            STATUS_SUBMITTED, STATUS_FILLED, STATUS_CANCELLED)
ACTIVE_STATUSES = (STATUS_PENDING_CONFIRMATION, STATUS_APPROVED, STATUS_SUBMITTED)
COUNTED_BUY_STATUSES = ACTIVE_STATUSES + (STATUS_FILLED,)


class DuplicateOrderError(ValueError):
    """同 (as_of, symbol, side) 已存在活跃订单。"""


def has_active_order(session: Session, as_of: dt.date, symbol: str, side: str) -> bool:
    stmt = (select(OrderRow.id)
            .where(OrderRow.as_of == as_of, OrderRow.symbol == symbol,
                   OrderRow.side == side, OrderRow.status.in_(ACTIVE_STATUSES))
            .limit(1))
    return session.scalars(stmt).first() is not None


def create_order(session: Session, as_of: dt.date, symbol: str, side: str, shares: int,
                 status: str, mode: str, decision_id=None, reason: str = "") -> OrderRow:
    if status not in STATUSES:
        raise ValueError(f"invalid status: {status}")
    if status in ACTIVE_STATUSES and has_active_order(session, as_of, symbol, side):
        raise DuplicateOrderError(
            f"active {side} order already exists for {symbol} on {as_of}")
    row = OrderRow(as_of=as_of, symbol=symbol, side=side, shares=shares,
                   status=status, mode=mode, decision_id=decision_id, reason=reason)
    session.add(row)
    session.flush()
    return row


def get_order(session: Session, order_id: int):
    return session.get(OrderRow, order_id)


def get_orders_by_status(session: Session, status: str) -> list:
    stmt = select(OrderRow).where(OrderRow.status == status).order_by(OrderRow.id)
    return list(session.scalars(stmt))


def update_status(session: Session, order_id: int, status: str, reason: str = "") -> OrderRow:
    if status not in STATUSES:
        raise ValueError(f"invalid status: {status}")
    row = session.get(OrderRow, order_id)
    if row is None:
        raise ValueError(f"order {order_id} not found")
    row.status = status
    if reason:
        row.reason = reason
    session.flush()
    return row


def buy_symbols_today(session: Session, as_of: dt.date) -> set:
    """当日计入"新开仓数"的买单标的集合(活跃 + 已成交;拒绝/撤销不计)。"""
    stmt = (select(OrderRow.symbol)
            .where(OrderRow.as_of == as_of, OrderRow.side == "buy",
                   OrderRow.status.in_(COUNTED_BUY_STATUSES))
            .distinct())
    return set(session.scalars(stmt))
