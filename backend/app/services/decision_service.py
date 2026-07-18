"""委员会决定的服务端校验与落库。

安全红线:schema 校验在服务端强制执行,LLM/调用方不可绕过;
M2 只有建议模式(advisory)——只落库进日报,不生成订单;
mode 字段已留好,M3 在此按 DB 模式开关分流到风控闸门/订单管理。
"""
import datetime as dt
import json

from sqlalchemy.orm import Session

from app.store.repos.decision_repo import save_decision

ACTIONS = ("buy", "sell", "hold")
ROLE_KEYS = ("technical", "fundamental", "sentiment", "bear")
MODE_ADVISORY = "advisory"


class DecisionValidationError(ValueError):
    """submit_decision 的 payload 不合规。"""


def _require(cond, msg: str) -> None:
    if not cond:
        raise DecisionValidationError(msg)


def _require_text(value, msg: str) -> None:
    _require(isinstance(value, str) and value.strip(), msg)


def validate_decision(payload) -> dict:
    """校验并归一化 payload;不合规抛 DecisionValidationError。"""
    _require(isinstance(payload, dict), "payload must be a dict")
    symbol = payload.get("symbol")
    _require_text(symbol, "symbol must be a non-empty string")
    try:
        as_of = dt.date.fromisoformat(str(payload.get("as_of")))
    except ValueError:
        raise DecisionValidationError("as_of must be an ISO date (YYYY-MM-DD)") from None
    _require(payload.get("action") in ACTIONS, f"action must be one of {ACTIONS}")
    confidence = payload.get("confidence")
    _require(isinstance(confidence, (int, float)) and not isinstance(confidence, bool)
             and 0.0 <= float(confidence) <= 1.0, "confidence must be a number in [0, 1]")
    committee = payload.get("committee")
    _require(isinstance(committee, dict), "committee must be a dict with four role sections")
    for role in ROLE_KEYS:
        section = committee.get(role)
        _require(isinstance(section, dict), f"committee.{role} section is required")
        _require_text(section.get("summary"), f"committee.{role}.summary must be non-empty")
    chair = payload.get("chair")
    _require(isinstance(chair, dict), "chair section is required")
    _require_text(chair.get("verdict"), "chair.verdict must be non-empty")
    _require_text(chair.get("bear_rebuttal"),
                  "chair.bear_rebuttal must be non-empty (裁决必须显式回应空头)")
    normalized = dict(payload)
    normalized["symbol"] = symbol.strip().upper()
    normalized["as_of"] = as_of.isoformat()
    normalized["confidence"] = float(confidence)
    normalized["mode"] = MODE_ADVISORY  # M2 服务端强制建议模式,调用方传入无效
    return normalized


def submit_decision(session: Session, payload) -> dict:
    """校验 → 落库 → commit。M2 建议模式:不生成订单。"""
    normalized = validate_decision(payload)
    row = save_decision(
        session,
        as_of=dt.date.fromisoformat(normalized["as_of"]),
        symbol=normalized["symbol"],
        action=normalized["action"],
        confidence=normalized["confidence"],
        mode=normalized["mode"],
        payload_json=json.dumps(normalized, ensure_ascii=False),
    )
    session.commit()
    return {
        "status": "recorded",
        "id": row.id,
        "mode": row.mode,
        "symbol": row.symbol,
        "action": row.action,
        "as_of": normalized["as_of"],
        "note": "M2 建议模式:已落库并将进入日报,不生成订单",
    }
