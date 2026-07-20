"""GET /api/decisions + GET /api/performance —— 只读:决策历史浏览 + 从
trade_review 复盘条目聚合出的业绩战绩单(这是判断委员会长期是否靠谱的起点)。

只读约定(与 /api/dashboard、/api/memory 一致):GET 不设 token 门禁,不写库
(不调用 session.commit()),不发起任何网络请求——/api/performance 的权益
用持仓 avg_cost 近似(与 /api/dashboard 同一约定),不取实时行情,因此不含
未实现的盯市盈亏(mark-to-market)。

payload_json / evidence_json 都是外部写入的自由格式 JSON;这里全程防御式解析
(json.loads 失败、类型不对、字段缺失都当作"没有"处理),绝不因脏数据 500。
"""
import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.store.models import DecisionRow, MemoryEntryRow
from app.store.repos.decision_repo import get_recent_decisions
from app.store.repos.memory_repo import get_entries
from app.store.repos.paper_repo import get_account, get_positions
from app.store.repos.settings_repo import get_app_settings

router = APIRouter(tags=["history"])

CHAIR_VERDICT_MAX_LEN = 300


def _chair_verdict(payload_json: str) -> str:
    """从 payload_json 里取 chair.verdict;缺失/格式不对一律 "",绝不抛错。"""
    try:
        payload = json.loads(payload_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    chair = payload.get("chair")
    if not isinstance(chair, dict):
        return ""
    verdict = chair.get("verdict")
    if not isinstance(verdict, str):
        return ""
    return verdict[:CHAIR_VERDICT_MAX_LEN]


def _decision_to_dict(row: DecisionRow) -> dict:
    return {
        "id": row.id,
        "as_of": row.as_of.isoformat(),
        "symbol": row.symbol,
        "action": row.action,
        "confidence": row.confidence,
        "mode": row.mode,
        "chair_verdict": _chair_verdict(row.payload_json),
        "created_at": row.created_at.isoformat(),
    }


@router.get("/decisions")
def list_decisions_route(symbol: str | None = None,
                         limit: int = Query(50, ge=1, le=500),
                         session: Session = Depends(get_session)) -> list:
    sym = (symbol or "").strip().upper() or None
    rows = get_recent_decisions(session, symbol=sym, limit=limit)
    return [_decision_to_dict(r) for r in rows]


def _parse_review_evidence(row: MemoryEntryRow) -> dict | None:
    """防御式解析 trade_review 的 evidence_json;不可解析/非 dict/realized_pnl
    非数字一律当作"不可用"跳过(不计入 closed_trades),绝不抛错。"""
    try:
        evidence = json.loads(row.evidence_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(evidence, dict):
        return None
    pnl = evidence.get("realized_pnl")
    if not isinstance(pnl, (int, float)) or isinstance(pnl, bool):
        return None
    return evidence


def _series_sort_key(row: MemoryEntryRow, evidence: dict) -> str:
    """排序键:evidence 里若带 date 字段用它,否则退回该复盘条目的 created_at。"""
    ev_date = evidence.get("date")
    if isinstance(ev_date, str) and ev_date:
        return ev_date
    return row.created_at.isoformat()


def _cumulative_pnl_series(parsed: list[tuple[MemoryEntryRow, dict]]) -> list[dict]:
    ordered = sorted(parsed, key=lambda pair: _series_sort_key(*pair))
    running = 0.0
    by_date: dict[str, float] = {}
    for row, evidence in ordered:
        running += evidence["realized_pnl"]
        by_date[row.created_at.date().isoformat()] = running  # 同日只留最后累计值
    return [{"date": date, "cum_pnl": cum_pnl} for date, cum_pnl in by_date.items()]


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


@router.get("/performance")
def performance_route(session: Session = Depends(get_session)) -> dict:
    reviews = get_entries(session, kind="trade_review")
    parsed: list[tuple[MemoryEntryRow, dict]] = []
    for row in reviews:
        evidence = _parse_review_evidence(row)
        if evidence is not None:
            parsed.append((row, evidence))

    closed_trades = len(parsed)
    pnls = [evidence["realized_pnl"] for _, evidence in parsed]
    realized_pnl_total = sum(pnls)
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    win_rate = wins / closed_trades if closed_trades else None
    avg_win = _mean([p for p in pnls if p > 0])
    avg_loss = _mean([p for p in pnls if p < 0])
    holding_days = [
        evidence["holding_days"] for _, evidence in parsed
        if isinstance(evidence.get("holding_days"), (int, float))
        and not isinstance(evidence.get("holding_days"), bool)
    ]
    avg_holding_days = _mean(holding_days)

    settings_row = get_app_settings(session)
    account = get_account(session, settings_row.initial_cash)
    positions = get_positions(session)
    # 权益用持仓 avg_cost 近似(与 /api/dashboard 同一约定):不发起行情请求,
    # 因此不含未实现的盯市盈亏(mark-to-market)。
    open_positions_cost_value = sum(p.shares * p.avg_cost for p in positions.values())
    equity_at_cost = account.cash + open_positions_cost_value

    return {
        "closed_trades": closed_trades,
        "realized_pnl_total": realized_pnl_total,
        "win_rate": win_rate,
        "wins": wins,
        "losses": losses,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "avg_holding_days": avg_holding_days,
        "cumulative_pnl_series": _cumulative_pnl_series(parsed),
        "cash": account.cash,
        "open_positions": len(positions),
        "open_positions_cost_value": open_positions_cost_value,
        "equity_at_cost": equity_at_cost,
        "initial_cash": settings_row.initial_cash,
    }
