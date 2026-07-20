"""GET/POST /api/orders —— 薄壳,镜像 app/cli_trading.py 的 approve/reject 语义。

安全红线:approve 端点不接受任何请求体——as_of 与参考价永远服务端派生
(et_trading_day + latest_closes_for),客户端不存在任何覆盖通道;
approve_order 在批准时刻重新过 RiskGate,本模块绝不触碰闸门逻辑。
"""
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_provider, get_session
from app.api.schemas import RejectBody
from app.api.security import require_token
from app.data.base import PriceProvider
from app.execution.order_manager import (approve_order, list_pending, order_to_dict,
                                         reject_order)
from app.services.market_data_service import latest_closes_for
from app.store.repos.order_repo import get_order, get_orders_by_status
from app.store.repos.paper_repo import get_positions
from app.util.trading_day import et_trading_day

router = APIRouter(tags=["orders"])

_NOOP_MARKER = "is not pending confirmation"


@router.get("/orders")
def list_orders_route(status: str | None = None,
                      session: Session = Depends(get_session)) -> list:
    if status:
        return [order_to_dict(r) for r in get_orders_by_status(session, status)]
    return list_pending(session)


@router.post("/orders/{order_id}/approve", dependencies=[Depends(require_token)])
def approve_route(order_id: int, session: Session = Depends(get_session),
                  provider: PriceProvider = Depends(get_provider)) -> dict:
    as_of = et_trading_day(dt.datetime.now(dt.UTC))
    order = get_order(session, order_id)
    symbols = sorted(({order.symbol} if order else set()) | set(get_positions(session)))
    prices = latest_closes_for(provider, symbols, as_of) if symbols else {}
    result = approve_order(session, order_id, as_of, prices)
    session.commit()
    if _NOOP_MARKER in result["note"]:
        raise HTTPException(status_code=409, detail=result["note"])
    return result


@router.post("/orders/{order_id}/reject", dependencies=[Depends(require_token)])
def reject_route(order_id: int, body: RejectBody = RejectBody(),
                 session: Session = Depends(get_session)) -> dict:
    result = reject_order(session, order_id, body.reason)
    session.commit()
    if _NOOP_MARKER in result["note"]:
        raise HTTPException(status_code=409, detail=result["note"])
    return result
