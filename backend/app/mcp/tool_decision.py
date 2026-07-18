from app.mcp import runtime
from app.services.decision_service import DecisionValidationError
from app.services.decision_service import submit_decision as _submit_decision


def submit_decision(payload: dict) -> dict:
    """提交委员会结构化决定。服务端强制校验;M2 建议模式:仅落库,不生成订单。

    校验失败返回 {"status": "rejected", "error": ...}(不抛异常,便于 agent 修正重试)。
    """
    with runtime.open_session() as session:
        try:
            return _submit_decision(session, payload)
        except DecisionValidationError as exc:
            return {"status": "rejected", "error": str(exc)}
