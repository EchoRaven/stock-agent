"""GET/POST/DELETE /api/watchlist —— 持久自选股清单 + 实时报价。

只读约定(与 dashboard/history/marks 一致):GET 不设 token 门禁,批量取价
(fetch_bars,一次调用覆盖全部自选标的)失败(provider 抛错/网络问题)一律
降级为该标的全部价格字段 None,绝不 500——payload 没有价格通道,价格只走
服务端 provider(get_provider,见 app/api/deps.py)。

POST(新增/更新自选)与 DELETE(移除自选,本仓库第一个 DELETE 端点)都是
状态变更,挂 Depends(require_token) 门禁(见 app/api/security.py)。
"""
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_provider, get_session
from app.api.schemas import WatchlistCreate
from app.api.security import require_token
from app.data.base import PriceProvider
from app.services.market_data_service import fetch_bars
from app.store.models import WatchlistRow
from app.store.repos.watchlist_repo import add, list_all, remove
from app.util.trading_day import et_trading_day

router = APIRouter(tags=["watchlist"])

_LOOKBACK_DAYS = 10


def _base_dict(row: WatchlistRow) -> dict:
    return {"symbol": row.symbol, "note": row.note, "added_at": row.added_at.isoformat()}


def _price_fields(bars) -> dict:
    """从一段升序日线里算 current/prev/change/change_pct;bars 缺失或不足两根
    (算不出环比)一律全部 None,绝不半算半 None。"""
    if bars is None or len(bars) < 2:
        return {"current_price": None, "prev_close": None, "change": None, "change_pct": None}
    current_price = float(bars["close"].iloc[-1])
    prev_close = float(bars["close"].iloc[-2])
    change = current_price - prev_close
    change_pct = (change / prev_close * 100) if prev_close else None
    return {"current_price": current_price, "prev_close": prev_close,
            "change": change, "change_pct": change_pct}


@router.get("/watchlist")
def list_watchlist_route(session: Session = Depends(get_session),
                         price_provider: PriceProvider = Depends(get_provider)) -> list:
    rows = list_all(session)
    if not rows:
        return []

    as_of = et_trading_day(dt.datetime.now(dt.UTC))
    symbols = [r.symbol for r in rows]
    try:
        bars_by_symbol, _skipped = fetch_bars(
            price_provider, symbols, as_of - dt.timedelta(days=_LOOKBACK_DAYS), as_of)
    except Exception:
        # 取价整体失败(provider 抛错/网络问题)降级为全部 None,绝不 500。
        bars_by_symbol = {}

    return [dict(_base_dict(row), **_price_fields(bars_by_symbol.get(row.symbol)))
           for row in rows]


@router.post("/watchlist", dependencies=[Depends(require_token)])
def add_watchlist_route(body: WatchlistCreate, session: Session = Depends(get_session)) -> dict:
    symbol = body.symbol.strip().upper()
    if not (1 <= len(symbol) <= 16):
        raise HTTPException(status_code=400, detail="symbol must be 1..16 chars")
    row = add(session, symbol, note=body.note)
    session.commit()
    return _base_dict(row)


@router.delete("/watchlist/{symbol}", dependencies=[Depends(require_token)])
def remove_watchlist_route(symbol: str, session: Session = Depends(get_session)) -> dict:
    removed = remove(session, symbol)
    session.commit()
    return {"removed": removed, "symbol": symbol.strip().upper()}
