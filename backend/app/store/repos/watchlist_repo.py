"""自选股清单仓储。symbol 统一以 strip+upper 后的形式存取/比较,调用方传入
大小写混杂的输入不会产生重复行。"""
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.store.models import WatchlistRow


def _normalize(symbol: str) -> str:
    return symbol.strip().upper()


def add(session: Session, symbol: str, note: str | None = None) -> WatchlistRow:
    """UPSERT:symbol 已存在则更新其 note(仅当 note is not None,否则保留旧值)
    并返回该行;否则插入新行。"""
    sym = _normalize(symbol)
    row = session.scalar(select(WatchlistRow).where(WatchlistRow.symbol == sym))
    if row is not None:
        if note is not None:
            row.note = note
        session.flush()
        return row
    row = WatchlistRow(symbol=sym, note=note)
    session.add(row)
    session.flush()
    return row


def remove(session: Session, symbol: str) -> bool:
    """按(大写化)symbol 删除;真删到行返回 True,否则 False。"""
    sym = _normalize(symbol)
    result = session.execute(delete(WatchlistRow).where(WatchlistRow.symbol == sym))
    session.flush()
    return result.rowcount > 0


def list_all(session: Session) -> list[WatchlistRow]:
    """按加入时间倒序(最新在前);id.desc() 作为同时间戳的确定性 tiebreak。"""
    stmt = select(WatchlistRow).order_by(WatchlistRow.added_at.desc(), WatchlistRow.id.desc())
    return list(session.scalars(stmt))


def exists(session: Session, symbol: str) -> bool:
    sym = _normalize(symbol)
    return session.scalar(select(WatchlistRow).where(WatchlistRow.symbol == sym)) is not None
