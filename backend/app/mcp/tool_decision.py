import datetime as dt

from app.mcp import runtime
from app.services.decision_service import DecisionValidationError
from app.services.decision_service import submit_decision as _submit_decision
from app.services.market_data_service import latest_closes_for
from app.store.repos.paper_repo import get_positions
from app.store.repos.settings_repo import MODE_ADVISORY, get_mode
from app.util.trading_day import et_trading_day


def _gate_prices(session, payload, now_utc: dt.datetime) -> dict:
    """非 advisory 模式下为闸门取最新参考价(决策标的 + 全部持仓)。

    价格由服务端 provider 取,调用方 payload 没有价格通道(安全红线)。
    """
    if not isinstance(payload, dict) or get_mode(session) == MODE_ADVISORY:
        return {}
    symbols = {str(payload.get("symbol", "")).strip().upper()} | set(get_positions(session))
    symbols.discard("")
    if not symbols:
        return {}
    as_of = et_trading_day(now_utc)
    return latest_closes_for(runtime.get_price_provider(), sorted(symbols), as_of)


def submit_decision(payload: dict) -> dict:
    """提交委员会结构化决定。mode 唯一真相在 DB settings;payload 传 mode 无效。

    校验失败返回 {"status": "rejected", "error": ...}(不抛异常,便于 agent 修正重试)。
    单一 now_utc 同时驱动取价日期与闸门 as_of(server-derived,payload.as_of 无通道)。
    """
    with runtime.open_session() as session:
        now_utc = dt.datetime.now(dt.UTC)
        try:
            return _submit_decision(session, payload,
                                    prices=_gate_prices(session, payload, now_utc),
                                    now_utc=now_utc)
        except DecisionValidationError as exc:
            return {"status": "rejected", "error": str(exc)}
