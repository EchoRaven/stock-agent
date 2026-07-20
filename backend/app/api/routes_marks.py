"""GET /api/positions/marks —— 只读:按最新收盘价盯市重估持仓的未实现盈亏。

与 /api/dashboard 刻意分开:dashboard 用 avg_cost 近似、离线、快,给轮询用;
这里会发起真实取价请求(有网络延迟/失败的可能),供前端作为二次异步拉取。
只读约定(与 dashboard/history 一致):不落库、不设 token 门禁。取价失败
(provider 抛错/网络问题)一律降级为全部 unpriced,绝不 500——payload 没有
价格通道,价格只走服务端 provider(get_provider,见 app/api/deps.py)。
"""
import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_provider, get_session
from app.data.base import PriceProvider
from app.services.market_data_service import latest_closes_for
from app.store.repos.paper_repo import get_account, get_positions
from app.store.repos.settings_repo import get_app_settings
from app.util.trading_day import et_trading_day

router = APIRouter(tags=["marks"])


@router.get("/positions/marks")
def marks_route(session: Session = Depends(get_session),
                price_provider: PriceProvider = Depends(get_provider)) -> dict:
    as_of = et_trading_day(dt.datetime.now(dt.UTC))
    settings_row = get_app_settings(session)
    account = get_account(session, settings_row.initial_cash)
    positions = get_positions(session)

    if not positions:
        return {
            "as_of": as_of.isoformat(),
            "positions": [],
            "priced": 0,
            "unpriced": [],
            "total_cost": 0.0,
            "total_market_value": 0.0,
            "total_unrealized": 0.0,
            "total_unrealized_pct": None,
            "cash": account.cash,
            "equity": account.cash,
        }

    symbols = sorted(positions)
    try:
        prices = latest_closes_for(price_provider, symbols, as_of)
    except Exception:
        # 取价失败(provider 抛错/网络问题)降级为全部 unpriced,绝不 500。
        prices = {}

    out_positions = []
    unpriced = []
    total_cost = 0.0
    total_market_value = 0.0
    unpriced_cost = 0.0

    for symbol in symbols:
        row = positions[symbol]
        shares = row.shares
        avg_cost = row.avg_cost
        cost_basis = shares * avg_cost
        price = prices.get(symbol)
        is_priced = price is not None
        market_value = shares * price if is_priced else None
        unrealized = (market_value - cost_basis) if is_priced else None
        unrealized_pct = (unrealized / cost_basis * 100
                          if is_priced and cost_basis > 0 else None)

        out_positions.append({
            "symbol": symbol,
            "shares": shares,
            "avg_cost": avg_cost,
            "cost_basis": cost_basis,
            "current_price": price,
            "market_value": market_value,
            "unrealized": unrealized,
            "unrealized_pct": unrealized_pct,
        })

        if is_priced:
            total_cost += cost_basis
            total_market_value += market_value
        else:
            unpriced.append(symbol)
            unpriced_cost += cost_basis

    total_unrealized = total_market_value - total_cost
    total_unrealized_pct = (total_unrealized / total_cost * 100
                            if total_cost > 0 else None)

    # equity: 未定价持仓退回用 cost 近似,保证 equity 总是完整可算,不因个别
    # 标的取价失败就漏掉那块仓位的价值。
    equity = account.cash + total_market_value + unpriced_cost

    return {
        "as_of": as_of.isoformat(),
        "positions": out_positions,
        "priced": len(symbols) - len(unpriced),
        "unpriced": unpriced,
        "total_cost": total_cost,
        "total_market_value": total_market_value,
        "total_unrealized": total_unrealized,
        "total_unrealized_pct": total_unrealized_pct,
        "cash": account.cash,
        "equity": equity,
    }
