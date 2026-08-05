"""委员会决定的服务端校验、落库与模式分流。

安全红线:
- schema 校验在服务端强制执行,LLM/调用方不可绕过;
- mode 的唯一真相在 DB settings row:payload 里的 mode 一律剥掉,
  未知/未设一律按 advisory 处理(fail-safe);
- 非 advisory 模式经 order_manager 单一 choke point 分流,任何订单必过风控闸门;
- prices 由服务端注入(MCP 工具层/CLI 取行情),payload 没有价格通道;
- 闸门/下单用的 as_of 由服务端时钟(now_utc,可注入便于测试)经 et_trading_day
  派生,绝不采信 payload 的 as_of(可被伪造成未来日期以绕过熔断/冷却/查重——
  M3 final review 确认漏洞);payload 的 as_of 只落入 DecisionRow 作审计记录。
"""
import datetime as dt
import json

from sqlalchemy.orm import Session

from app.execution.order_manager import handle_decision
from app.store.repos.decision_repo import save_decision
from app.store.repos.paper_repo import get_positions
from app.store.repos.settings_repo import MODE_ADVISORY, get_mode
from app.util.trading_day import et_trading_day

ACTIONS = ("buy", "sell", "hold")
TRADE_ACTIONS = ("buy", "sell")
ROLE_KEYS = ("technical", "fundamental", "sentiment", "bear")


class DecisionValidationError(ValueError):
    """submit_decision 的 payload 不合规。"""


def _require(cond, msg: str) -> None:
    if not cond:
        raise DecisionValidationError(msg)


def _require_text(value, msg: str) -> None:
    _require(isinstance(value, str) and value.strip(), msg)


def validate_decision(payload) -> dict:
    """校验并归一化 payload;不合规抛 DecisionValidationError。mode 字段一律剥掉。"""
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
    if payload.get("action") in TRADE_ACTIONS:
        shares = payload.get("shares")
        _require(isinstance(shares, int) and not isinstance(shares, bool) and shares > 0,
                 "shares must be a positive integer for buy/sell decisions")
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
    normalized.pop("mode", None)  # 安全红线:mode 唯一真相在 DB,不信任调用方
    normalized["symbol"] = symbol.strip().upper()
    normalized["as_of"] = as_of.isoformat()
    normalized["confidence"] = float(confidence)
    return normalized


def submit_decision(session: Session, payload, prices: dict | None = None,
                    now_utc: dt.datetime | None = None) -> dict:
    """校验 → 从 DB 读 mode(唯一真相)→ 落库 → 按模式分流订单。

    now_utc 可注入(测试确定性,与 watchdog/circuit_breaker 同款时间注入模式)。

    ⚠️ Finding A(安全模型里唯一一处靠纵深防御、而非局部不变量守的地方):
    本函数**信任并直接透传调用方给的 `shares`**(见下方 handle_decision 调用),
    并不在这里服务端重算或校验它。字面红线#2「股数永远服务端算」靠的是**下游兜底**——
    买入超限被 RiskGate 的单票/总仓上限规则拒单、卖出被 PaperBroker._execute 的
    `min(order.shares, held)` 封顶。因此 contained(无现实 exploit),我们没做高风险
    的 sizing 契约重构,而是用回归测试把遏制性质钉死:
      tests/services/test_decision_service_m3.py::test_finding_a_oversized_buy_shares_cannot_exceed_single_position_cap
      tests/services/test_decision_service_m3.py::test_finding_a_oversized_sell_shares_cannot_oversell
    **改动 RiskGate 或 settle_open 逻辑的人注意**:你可能在不知情下拆掉这层兜底——
    上面两个测试会变红,别绕过它们,先读 docs/ROADMAP「已知问题」的 Finding A。
    """
    normalized = validate_decision(payload)
    mode = get_mode(session)  # fail-safe:未知/未设 → advisory
    normalized["mode"] = mode
    # scorecard 记分卡的分母需要知道提交时是否真的持有该 symbol(sell 只有
    # held 时才结构上可能)——按实际持仓派生,不信任 payload 的 action。
    held = normalized["symbol"] in get_positions(session)
    row = save_decision(
        session,
        as_of=dt.date.fromisoformat(normalized["as_of"]),
        symbol=normalized["symbol"],
        action=normalized["action"],
        confidence=normalized["confidence"],
        mode=mode,
        payload_json=json.dumps(normalized, ensure_ascii=False),
        held=held,
    )
    result = {"status": "recorded", "id": row.id, "mode": mode, "symbol": row.symbol,
              "action": row.action, "as_of": normalized["as_of"]}
    if mode == MODE_ADVISORY or row.action == "hold":
        session.commit()
        result["note"] = "advisory/hold:已落库并将进入日报,不生成订单"
        return result
    # 安全红线:gate_as_of 由服务端时钟派生,绝不用 row.as_of(来自 payload,可伪造)
    gate_as_of = et_trading_day(now_utc or dt.datetime.now(dt.UTC))
    routed = handle_decision(session, row, mode, normalized["shares"], prices or {},
                             as_of=gate_as_of)
    session.commit()
    result.update(routed)
    return result
