"""GET /api/market/regime —— 只读:大盘(SPY)相对其 200 日均线的风险开关状态。

无 LLM、纯规则信号,只做市场背景参考展示,不驱动下单、不落库。只读约定(与
marks/dashboard/history 一致):不设 token 门禁。

计算逻辑已抽到 app/services/market_regime_service.py(get_regime)——同一份逻辑
也被 app/services/committee_service.py 的委员会 prompt 复用(ADVISORY CONTEXT
ONLY 的宏观背景,见 trade_cycle_service/picks_service/routes_stock 的调用点),
这里的路由体只是原样转发,不改变响应形状(取价失败一律降级为
available=False,绝不 500——见 get_regime 文档)。
"""
from fastapi import APIRouter, Depends

from app.api.deps import get_provider
from app.data.base import PriceProvider
from app.services.market_regime_service import get_regime

router = APIRouter(tags=["market"])


@router.get("/market/regime")
def market_regime_route(price_provider: PriceProvider = Depends(get_provider)) -> dict:
    return get_regime(price_provider)
