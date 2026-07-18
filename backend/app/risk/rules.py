"""风控规则:一条规则一个类,check 为纯函数(不触 DB、不触网),便于全覆盖单测。

AccountState 由 execution/account_state.py 从 DB + 最新价组装——
规则永远不信任调用方 payload 里的任何数字。
"""
import datetime as dt
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class OrderRequest:
    """闸门评估用的订单请求。price 为服务端取的最新参考价(买入估值必需)。"""

    symbol: str
    side: str  # "buy" | "sell"
    shares: int
    price: float
    as_of: dt.date


@dataclass(frozen=True)
class AccountState:
    """闸门评估用的账户快照。"""

    cash: float
    position_values: dict  # symbol -> 市值
    new_buy_symbols_today: frozenset = frozenset()
    last_sell_dates: dict = field(default_factory=dict)
    breaker_tripped: bool = False
    stale_priced_symbols: frozenset = frozenset()  # 持仓中只能用 avg_cost 估值的标的

    def equity(self) -> float:
        return self.cash + sum(self.position_values.values())


@dataclass(frozen=True)
class RiskParams:
    single_position_cap_pct: float
    total_position_cap_pct: float
    max_new_positions_per_day: int
    daily_loss_halt_pct: float
    cooldown_days: int


@dataclass(frozen=True)
class RiskCheck:
    allowed: bool
    reason: str


ALLOW = RiskCheck(True, "")


class RiskRule(ABC):
    name: str = "risk_rule"

    @abstractmethod
    def check(self, order: OrderRequest, account: AccountState,
              params: RiskParams) -> RiskCheck:
        """允许返回 ALLOW;拒绝返回 RiskCheck(False, 原因)。"""


class CircuitBreakerRule(RiskRule):
    """日亏损熔断:触发当日只允许卖出。"""

    name = "circuit_breaker"

    def check(self, order, account, params):
        if account.breaker_tripped and order.side != "sell":
            return RiskCheck(False, "daily-loss circuit breaker tripped: "
                                    f"only sells allowed on {order.as_of}")
        return ALLOW


class StaleQuoteRule(RiskRule):
    """持仓报价缺失 fail-safe:权益不可信 → 暂停新开仓,仅允许卖出(与熔断同姿态)。

    评审 finding #6:用 avg_cost 顶替缺失报价会高估权益、低估回撤,可能让熔断该触发
    却没触发、且其他标的买单被放行。有持仓报价缺失即保守拒买。
    """

    name = "stale_quote"

    def check(self, order, account, params):
        if order.side == "buy" and account.stale_priced_symbols:
            return RiskCheck(False, "持仓报价缺失,无法可信计算权益,保守起见暂停新开仓"
                                    f"(仅允许卖出);stale={sorted(account.stale_priced_symbols)}")
        return ALLOW


class SinglePositionCapRule(RiskRule):
    """单票市值上限(占权益比例)。"""

    name = "single_position_cap"

    def check(self, order, account, params):
        if order.side != "buy":
            return ALLOW
        target = account.position_values.get(order.symbol, 0.0) + order.shares * order.price
        cap = account.equity() * params.single_position_cap_pct
        if target > cap:
            return RiskCheck(False, f"single-position cap: {order.symbol} "
                                    f"target value {target:.2f} > cap {cap:.2f}")
        return ALLOW


class TotalPositionCapRule(RiskRule):
    """总仓位上限(占权益比例)。"""

    name = "total_position_cap"

    def check(self, order, account, params):
        if order.side != "buy":
            return ALLOW
        target = sum(account.position_values.values()) + order.shares * order.price
        cap = account.equity() * params.total_position_cap_pct
        if target > cap:
            return RiskCheck(False, f"total-position cap: target exposure "
                                    f"{target:.2f} > cap {cap:.2f}")
        return ALLOW


class MaxNewPositionsRule(RiskRule):
    """单日新开仓数上限。已持有标的加仓、当日已计数标的不算新开仓。"""

    name = "max_new_positions"

    def check(self, order, account, params):
        if order.side != "buy":
            return ALLOW
        opening = (order.symbol not in account.position_values
                   and order.symbol not in account.new_buy_symbols_today)
        if opening and len(account.new_buy_symbols_today) >= params.max_new_positions_per_day:
            return RiskCheck(False, "max new positions per day "
                                    f"({params.max_new_positions_per_day}) reached")
        return ALLOW


class CooldownRule(RiskRule):
    """同一标的冷却期:卖出后 cooldown_days 个日历日内不得回买。"""

    name = "cooldown"

    def check(self, order, account, params):
        if order.side != "buy":
            return ALLOW
        last_sell = account.last_sell_dates.get(order.symbol)
        if last_sell is not None and (order.as_of - last_sell).days < params.cooldown_days:
            return RiskCheck(False, f"cooldown: {order.symbol} sold on {last_sell}, "
                                    f"{params.cooldown_days}-day cooldown active")
        return ALLOW
