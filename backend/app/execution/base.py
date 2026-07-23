"""Broker 抽象。

安全红线:接口只有 buy/sell 订单的提交与撮合——系统内永不存在
转账/出金/提现方法(tests/execution/test_no_fund_egress.py 守卫)。
"""
import datetime as dt
from abc import ABC, abstractmethod

from sqlalchemy.orm import Session

from app.store.models import OrderRow


class Broker(ABC):
    """订单执行抽象:M3 只有 PaperBroker;M4+ 券商适配器实现同一接口。"""

    @abstractmethod
    def submit(self, session: Session, order: OrderRow) -> OrderRow:
        """把已获准订单标记为 submitted,等待下一交易时段开盘撮合。"""

    @abstractmethod
    def process_fills(self, session: Session, fill_date: dt.date, open_prices: dict,
                      restrict_to: set | None = None) -> list:
        """用 fill_date 开盘价撮合 submitted 订单,返回成交(PaperFillRow)列表。

        restrict_to 给出时只处理其中的 symbol,其余挂单原样保留(逐标的环内撮合
        用,避免单标的价格字典跨标的误 cancel);None 时保持全量撮合语义。"""
