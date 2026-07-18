"""风控闸门:服务端确定性执行,LLM/调用方只能"建议"。

安全红线:
- full_auto 下任何订单必须先过 gate.check,拒绝即不提交;
- gate 的输入只有服务端组装的 OrderRequest/AccountState/RiskParams,
  payload/工具参数没有任何通道进入判定,不可被绕过;
- 非法输入默认拒绝(default-deny);每次拒绝 logger.warning 留痕。
"""
import logging

from app.risk.rules import (ALLOW, AccountState, CircuitBreakerRule, CooldownRule,
                            MaxNewPositionsRule, OrderRequest, RiskCheck, RiskParams,
                            SinglePositionCapRule, StaleQuoteRule, TotalPositionCapRule)
from app.store.models import SettingsRow

logger = logging.getLogger(__name__)

DEFAULT_RULES = (CircuitBreakerRule(), StaleQuoteRule(), SinglePositionCapRule(),
                 TotalPositionCapRule(), MaxNewPositionsRule(), CooldownRule())


def params_from_row(row: SettingsRow) -> RiskParams:
    """DB settings 行 → 纯参数对象(规则层不接触 ORM)。"""
    return RiskParams(
        single_position_cap_pct=row.single_position_cap_pct,
        total_position_cap_pct=row.total_position_cap_pct,
        max_new_positions_per_day=row.max_new_positions_per_day,
        daily_loss_halt_pct=row.daily_loss_halt_pct,
        cooldown_days=row.cooldown_days,
    )


class RiskGate:
    """顺序执行所有 RiskRule,第一条拒绝即终止(熔断最先)。"""

    def __init__(self, rules: tuple = DEFAULT_RULES):
        self._rules = rules

    def check(self, order: OrderRequest, account: AccountState,
              params: RiskParams) -> RiskCheck:
        result = self._sanity(order)
        if result.allowed:
            for rule in self._rules:
                result = rule.check(order, account, params)
                if not result.allowed:
                    break
        if not result.allowed:
            logger.warning("risk gate rejected %s %s x%s: %s",
                           order.side, order.symbol, order.shares, result.reason)
        return result

    @staticmethod
    def _sanity(order: OrderRequest) -> RiskCheck:
        """default-deny:side/shares 非法、买单缺参考价,一律拒绝。"""
        if order.side not in ("buy", "sell"):
            return RiskCheck(False, f"invalid side {order.side!r}: denied by default")
        if order.shares <= 0:
            return RiskCheck(False, "shares must be positive: denied by default")
        if order.side == "buy" and order.price <= 0:
            return RiskCheck(False, "buy requires a positive reference price: denied by default")
        return ALLOW
