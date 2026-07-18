"""模拟盘账户/持仓/成交仓储。

安全红线:资金只在 cash ↔ 持仓之间流转;本模块没有、也永远不会有
转账/出金/提现类方法(tests/execution/test_no_fund_egress.py 全局守卫)。
"""
import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.store.models import PaperAccountRow, PaperFillRow, PaperPositionRow


def get_account(session: Session, initial_cash: float) -> PaperAccountRow:
    """取(或以 initial_cash 建)模拟盘账户单例行 id=1。"""
    row = session.get(PaperAccountRow, 1)
    if row is None:
        if initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        row = PaperAccountRow(id=1, cash=float(initial_cash))
        session.add(row)
        session.flush()
    return row


def get_positions(session: Session) -> dict:
    """symbol -> PaperPositionRow(仅 shares > 0)。"""
    stmt = select(PaperPositionRow).where(PaperPositionRow.shares > 0)
    return {row.symbol: row for row in session.scalars(stmt)}


def set_position(session: Session, symbol: str, shares: int, avg_cost: float) -> None:
    """写持仓(upsert);shares 归零即删行。"""
    stmt = select(PaperPositionRow).where(PaperPositionRow.symbol == symbol)
    row = session.scalars(stmt).first()
    if shares <= 0:
        if row is not None:
            session.delete(row)
    elif row is None:
        session.add(PaperPositionRow(symbol=symbol, shares=shares, avg_cost=float(avg_cost)))
    else:
        row.shares = shares
        row.avg_cost = float(avg_cost)
    session.flush()


def add_fill(session: Session, order_id: int, fill_date: dt.date, symbol: str,
             side: str, shares: int, price: float) -> PaperFillRow:
    row = PaperFillRow(order_id=order_id, fill_date=fill_date, symbol=symbol,
                       side=side, shares=shares, price=float(price))
    session.add(row)
    session.flush()
    return row


def get_fills(session: Session, fill_date: dt.date | None = None) -> list:
    stmt = select(PaperFillRow).order_by(PaperFillRow.id)
    if fill_date is not None:
        stmt = stmt.where(PaperFillRow.fill_date == fill_date)
    return list(session.scalars(stmt))


def last_sell_dates(session: Session) -> dict:
    """symbol -> 最近一次卖出成交日(冷却期规则的依据)。"""
    stmt = select(PaperFillRow).where(PaperFillRow.side == "sell")
    out: dict = {}
    for row in session.scalars(stmt):
        if row.symbol not in out or row.fill_date > out[row.symbol]:
            out[row.symbol] = row.fill_date
    return out
