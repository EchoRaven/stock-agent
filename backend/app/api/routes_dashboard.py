"""GET /api/dashboard —— 只读汇总:模式/持仓/权益/熔断/待批数量。

只读约定:不调用 execution/account_state.py 的 build_account_state
(它会用服务端取价重估权益并持久化熔断评估——那是闸门评估的副作用,不该被
一次 GET 轮询触发)。这里的 equity 用持仓 avg_cost 近似,不发起任何行情请求;
circuit_breaker 只读 is_tripped,不做 evaluate。
"""
import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.risk.circuit_breaker import is_tripped
from app.store.repos.order_repo import STATUS_PENDING_CONFIRMATION, get_orders_by_status
from app.store.repos.paper_repo import get_account, get_positions
from app.store.repos.settings_repo import get_app_settings, get_mode
from app.util.trading_day import et_trading_day

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard")
def dashboard_route(session: Session = Depends(get_session)) -> dict:
    as_of = et_trading_day(dt.datetime.now(dt.UTC))
    settings_row = get_app_settings(session)
    account = get_account(session, settings_row.initial_cash)
    positions = get_positions(session)
    position_value = sum(p.shares * p.avg_cost for p in positions.values())
    return {
        "mode": get_mode(session),
        "as_of": as_of.isoformat(),
        "positions": {sym: {"shares": p.shares, "avg_cost": p.avg_cost}
                      for sym, p in positions.items()},
        "cash": account.cash,
        "equity": account.cash + position_value,
        "circuit_breaker_tripped": is_tripped(account, as_of),
        "pending_orders_count": len(get_orders_by_status(session, STATUS_PENDING_CONFIRMATION)),
    }
