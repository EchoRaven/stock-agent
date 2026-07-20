"""GET /api/market/regime —— 只读:大盘(SPY)相对其 200 日均线的风险开关状态。

无 LLM、纯规则信号(复用 app/backtest/regime_signal.py 的 risk_on_asof),只做
市场背景参考展示,不驱动下单、不落库。只读约定(与 marks/dashboard/history
一致):不设 token 门禁。取价失败(provider 抛错/网络问题/无 SPY 数据)一律
降级为 available=False,绝不 500——payload 没有价格通道,价格只走服务端
provider(get_provider,见 app/api/deps.py)。
"""
import datetime as dt

from fastapi import APIRouter, Depends

from app.api.deps import get_provider
from app.backtest.regime_signal import risk_on_asof
from app.data.base import PriceProvider
from app.services.market_data_service import fetch_bars
from app.util.trading_day import et_trading_day

router = APIRouter(tags=["market"])

_UNAVAILABLE = {
    "available": False,
    "risk_on": None,
    "spy_close": None,
    "spy_sma200": None,
    "distance_pct": None,
}


@router.get("/market/regime")
def market_regime_route(price_provider: PriceProvider = Depends(get_provider)) -> dict:
    as_of = et_trading_day(dt.datetime.now(dt.UTC))

    try:
        bars, _skipped = fetch_bars(
            price_provider, ["SPY"], as_of - dt.timedelta(days=420), as_of
        )
    except Exception:
        # 取价失败(provider 抛错/网络问题)一律降级为无数据,绝不 500。
        bars = {}

    spy = bars.get("SPY")
    if spy is None or spy.empty:
        return {"as_of": as_of.isoformat(), **_UNAVAILABLE}

    risk_on = risk_on_asof(spy, as_of, 200)
    closes = spy["close"][spy.index.date <= as_of]
    spy_close = float(closes.iloc[-1])
    spy_sma200 = float(closes.iloc[-200:].mean()) if len(closes) >= 200 else None
    distance_pct = (
        (spy_close - spy_sma200) / spy_sma200 * 100 if spy_sma200 else None
    )

    return {
        "as_of": as_of.isoformat(),
        "available": True,
        "risk_on": risk_on,
        "spy_close": round(spy_close, 2),
        "spy_sma200": round(spy_sma200, 2) if spy_sma200 is not None else None,
        "distance_pct": round(distance_pct, 2) if distance_pct is not None else None,
    }
