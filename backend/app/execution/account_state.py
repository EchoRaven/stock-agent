"""从 DB + 最新价组装 AccountState——闸门判定的唯一事实来源。

安全红线:这里的每个数字都来自服务端(DB 持仓/现金/订单流水 + 服务端取价),
绝不采信调用方 payload;熔断在此统一评估并持久化。
"""
import datetime as dt

from sqlalchemy.orm import Session

from app.risk.circuit_breaker import evaluate
from app.risk.rules import AccountState
from app.store.repos.order_repo import buy_symbols_today
from app.store.repos.paper_repo import get_account, get_positions, last_sell_dates
from app.store.repos.settings_repo import get_app_settings


def build_account_state(session: Session, as_of: dt.date, prices: dict) -> AccountState:
    """持仓市值用最新价;缺价的持仓仍用 avg_cost 估值但记入 stale_priced_symbols
    (finding #6:权益不可信信号,StaleQuoteRule 据此拦买单);顺带完成熔断评估。"""
    settings_row = get_app_settings(session)
    account = get_account(session, settings_row.initial_cash)
    position_values = {}
    stale = []
    for symbol, row in get_positions(session).items():
        if symbol in prices:
            position_values[symbol] = row.shares * float(prices[symbol])
        else:
            position_values[symbol] = row.shares * float(row.avg_cost)
            stale.append(symbol)
    equity = account.cash + sum(position_values.values())
    tripped = evaluate(session, account, as_of, equity, settings_row.daily_loss_halt_pct)
    return AccountState(
        cash=account.cash,
        position_values=position_values,
        new_buy_symbols_today=frozenset(buy_symbols_today(session, as_of)),
        last_sell_dates=last_sell_dates(session),
        breaker_tripped=tripped,
        stale_priced_symbols=frozenset(stale),
    )
