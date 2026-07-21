"""GET /api/decisions + GET /api/decisions/scorecard + GET /api/decisions/forward-returns
+ GET /api/performance —— 只读:决策历史浏览 + 决策记分卡(委员会推荐是否有
区分度) + 前瞻收益记分卡(这些推荐到底对不对) + 从 trade_review 复盘条目
聚合出的业绩战绩单(这是判断委员会长期是否靠谱的起点)。

只读约定(与 /api/dashboard、/api/memory 一致):GET 不设 token 门禁,不写库
(不调用 session.commit()),不发起任何网络请求——/api/performance 的权益
用持仓 avg_cost 近似(与 /api/dashboard 同一约定),不取实时行情,因此不含
未实现的盯市盈亏(mark-to-market)。/api/decisions/scorecard 与
/api/decisions/forward-returns 都是纯聚合(见 app/services/scorecard_service.py),
同样不碰 LLM;forward-returns 唯一的"网络请求"是经由服务端行情源(deps.get_provider)
取历史收盘价,单标的失败/为空只影响该标的的统计(unpriced),不影响其余标的、
不抛异常。

路由顺序注意:/decisions/scorecard、/decisions/forward-returns 必须声明在任何
未来的 /decisions/{id} 风格路由之前,否则会被当成 id 路径参数吞掉(FastAPI 按
声明顺序匹配)——当前本模块还没有 /decisions/{id} 路由,但顺序先摆对,免得
以后加了忘记挪。

payload_json / evidence_json 都是外部写入的自由格式 JSON;这里全程防御式解析
(json.loads 失败、类型不对、字段缺失都当作"没有"处理),绝不因脏数据 500。
"""
import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_provider, get_session
from app.data.base import PriceProvider
from app.services.scorecard_service import DEFAULT_HORIZONS, build_forward_returns, build_scorecard
from app.store.models import DecisionRow, MemoryEntryRow
from app.store.repos.decision_repo import get_recent_decisions
from app.store.repos.memory_repo import get_entries
from app.store.repos.paper_repo import get_account, get_positions
from app.store.repos.settings_repo import get_app_settings

router = APIRouter(tags=["history"])

CHAIR_VERDICT_MAX_LEN = 300
MAX_FORWARD_RETURN_HORIZONS = 8


def _parse_horizons(raw: str | None) -> list[int]:
    """逗号分隔的正整数列表;非法/非正条目直接丢弃,全部丢弃或缺省时退回
    DEFAULT_HORIZONS——坏参数不能让端点 500 或返回空结果。"""
    if not raw:
        return list(DEFAULT_HORIZONS)
    out: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        try:
            value = int(part)
        except ValueError:
            continue
        if value <= 0:
            continue
        out.append(value)
        if len(out) >= MAX_FORWARD_RETURN_HORIZONS:
            break
    return out or list(DEFAULT_HORIZONS)


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


@router.get("/decisions/scorecard")
def decisions_scorecard_route(days: int | None = Query(None, ge=1),
                              session: Session = Depends(get_session)) -> dict:
    return build_scorecard(session, days=days)


@router.get("/decisions/forward-returns")
def decisions_forward_returns_route(
    horizons: str | None = Query(None),
    days: int | None = Query(None, ge=1),
    session: Session = Depends(get_session),
    provider: PriceProvider = Depends(get_provider),
) -> dict:
    return build_forward_returns(session, provider, horizons=tuple(_parse_horizons(horizons)),
                                 days=days)


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
