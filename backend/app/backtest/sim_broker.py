import datetime as dt
from dataclasses import dataclass


@dataclass(frozen=True)
class Order:
    symbol: str
    side: str  # "buy" | "sell"
    shares: int


@dataclass(frozen=True)
class Fill:
    date: dt.date
    symbol: str
    side: str
    shares: int
    price: float


class SimBroker:
    """模拟撮合:T 日提交的订单在下一次 process_fills(T+1 开盘)成交。"""

    def __init__(self, cash: float, slippage_bps: float = 5.0):
        if cash <= 0:
            raise ValueError("cash must be positive")
        self._cash = cash
        self._slip = slippage_bps / 10_000
        self._positions: dict = {}
        self._pending: list = []
        self.fills: list = []

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def positions(self) -> dict:
        return dict(self._positions)

    def position(self, symbol: str) -> int:
        return self._positions.get(symbol, 0)

    def submit(self, order: Order) -> None:
        if order.side not in ("buy", "sell"):
            raise ValueError(f"invalid side: {order.side}")
        if order.shares <= 0:
            raise ValueError("shares must be positive")
        self._pending.append(order)

    def process_fills(self, date: dt.date, open_prices: dict) -> list:
        """用当日开盘价撮合所有挂单;无开盘价(停牌等)的订单丢弃。"""
        todays: list = []
        for order in self._pending:
            price = open_prices.get(order.symbol)
            if price is None:
                continue
            fill = self._execute(order, date, float(price))
            if fill is not None:
                todays.append(fill)
        self._pending = []
        self.fills.extend(todays)
        return todays

    def _execute(self, order: Order, date: dt.date, open_price: float):
        if order.side == "buy":
            price = open_price * (1 + self._slip)
            shares = min(order.shares, int(self._cash // price))
            if shares <= 0:
                return None
            self._cash -= shares * price
            self._positions[order.symbol] = self.position(order.symbol) + shares
        else:
            price = open_price * (1 - self._slip)
            shares = min(order.shares, self.position(order.symbol))
            if shares <= 0:
                return None
            self._cash += shares * price
            remaining = self.position(order.symbol) - shares
            if remaining:
                self._positions[order.symbol] = remaining
            else:
                self._positions.pop(order.symbol, None)
        return Fill(date, order.symbol, order.side, shares, price)

    def equity(self, close_prices: dict) -> float:
        value = self._cash
        for sym, shares in self._positions.items():
            if sym not in close_prices:
                raise KeyError(f"missing close price for held symbol {sym}")
            value += shares * close_prices[sym]
        return value
