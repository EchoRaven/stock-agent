"""日亏损熔断:纯判定 + DB 持久化状态。

安全红线:熔断状态存 paper_account 行——同日重启不重置;
触发后当日只允许卖出(由 rules.CircuitBreakerRule 在闸门里执行)。
"""
import datetime as dt
import logging

from sqlalchemy.orm import Session

from app.store.models import PaperAccountRow

logger = logging.getLogger(__name__)


def should_trip(equity: float, day_start_equity: float, daily_loss_halt_pct: float) -> bool:
    """纯函数:当日权益回撤比例 >= 阈值即应熔断。"""
    if day_start_equity <= 0:
        return False
    return (day_start_equity - equity) / day_start_equity >= daily_loss_halt_pct


def is_tripped(account: PaperAccountRow, as_of: dt.date) -> bool:
    """熔断只对触发当日生效(次日自动恢复)。"""
    return account.breaker_tripped_on == as_of


def evaluate(session: Session, account: PaperAccountRow, as_of: dt.date,
             equity: float, daily_loss_halt_pct: float) -> bool:
    """滚动日起点快照 + 熔断判定;返回 as_of 当日是否处于熔断。

    已触发的当日即使权益回升也不解除(防抖动反复开闸)。
    """
    if account.day_start_date != as_of:
        account.day_start_date = as_of
        account.day_start_equity = float(equity)
        session.flush()
    if is_tripped(account, as_of):
        return True
    if should_trip(equity, account.day_start_equity, daily_loss_halt_pct):
        account.breaker_tripped_on = as_of
        session.flush()
        logger.warning("daily-loss circuit breaker tripped on %s (equity %.2f, day start %.2f)",
                       as_of, equity, account.day_start_equity)
        return True
    return False
