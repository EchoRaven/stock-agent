"""自建模拟盘:SimBroker 的 live 会话版——同一套下一开盘价撮合语义,状态持久化在 DB。

安全红线:只有 buy/sell 撮合;买入按现金截断、卖出按持仓截断;
资金只在 cash ↔ 持仓间流转,没有任何离开系统的路径。
"""
import datetime as dt
import logging

from sqlalchemy.orm import Session

from app.execution.base import Broker
from app.store.models import OrderRow
from app.store.repos import order_repo, paper_repo
from app.store.repos.settings_repo import get_app_settings

logger = logging.getLogger(__name__)


class PaperBroker(Broker):
    """T 日 submit 的订单在下一交易时段 process_fills(开盘价)成交,同 SimBroker 语义。"""

    def __init__(self, slippage_bps: float = 5.0):
        self._slip = slippage_bps / 10_000

    def submit(self, session: Session, order: OrderRow) -> OrderRow:
        if order.side not in ("buy", "sell"):
            raise ValueError(f"invalid side: {order.side}")
        if order.shares <= 0:
            raise ValueError("shares must be positive")
        return order_repo.update_status(session, order.id, order_repo.STATUS_SUBMITTED)

    def process_fills(self, session: Session, fill_date: dt.date, open_prices: dict) -> list:
        """撮合所有 submitted 订单;无法成交的一律 cancelled + reason(留痕,不静默)。"""
        account = paper_repo.get_account(session, get_app_settings(session).initial_cash)
        fills = []
        for order in order_repo.get_orders_by_status(session, order_repo.STATUS_SUBMITTED):
            price = open_prices.get(order.symbol)
            if price is None or price <= 0:
                order_repo.update_status(session, order.id, order_repo.STATUS_CANCELLED,
                                         reason=f"no valid open price on {fill_date}")
                continue
            fill = self._execute(session, account, order, fill_date, float(price))
            if fill is not None:
                fills.append(fill)
        session.flush()
        return fills

    def _execute(self, session, account, order: OrderRow, fill_date: dt.date,
                 open_price: float):
        held = paper_repo.get_position(session, order.symbol)
        held_shares = held.shares if held is not None else 0
        if order.side == "buy":
            price = open_price * (1 + self._slip)
            shares = min(order.shares, int(account.cash // price))
            if shares <= 0:
                order_repo.update_status(session, order.id, order_repo.STATUS_CANCELLED,
                                         reason="insufficient cash at fill time")
                return None
            account.cash -= shares * price
            prev_cost = held.avg_cost * held_shares if held is not None else 0.0
            total = held_shares + shares
            paper_repo.set_position(session, order.symbol, total,
                                    (prev_cost + shares * price) / total)
        else:
            price = open_price * (1 - self._slip)
            shares = min(order.shares, held_shares)
            if shares <= 0:
                order_repo.update_status(session, order.id, order_repo.STATUS_CANCELLED,
                                         reason="no position to sell at fill time")
                return None
            account.cash += shares * price
            paper_repo.set_position(session, order.symbol, held_shares - shares,
                                    held.avg_cost if held is not None else 0.0)
        order_repo.update_status(session, order.id, order_repo.STATUS_FILLED,
                                 reason=f"filled {shares} @ {price:.4f} on {fill_date}")
        return paper_repo.add_fill(session, order.id, fill_date, order.symbol,
                                   order.side, shares, price)
