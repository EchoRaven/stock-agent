"""Agent 知识库(Phase 2):平仓复盘——已实现盈亏事实优先,LLM 教训可选。

安全红线:ADVISORY CONTEXT ONLY,与 app/services/memory_service.py 同款——本模块
只读 app/store/repos/paper_repo.py 的成交流水与 app/store/repos/decision_repo.py
的历史决定,写 app/store/repos/memory_repo.py 的 memory_entries 表(kind=
"trade_review"),绝不导入、也绝不能被 app/execution/order_manager.py 或
app/risk/ 下任何下单/风控路径依赖(见 tests/test_memory_advisory_isolation.py
的自动化守卫)。

核心属性:
- 已实现盈亏(均价法)完全由 reconstruct_closed_trades 从成交流水确定性算出,
  从不采信 LLM 的任何数字;
- 幂等:以 sell_fill_id 为键,已写过复盘的平仓不会重复写入(见
  reflect_on_closed_trades 里对既有 trade_review.evidence_json 的扫描);
- LLM 教训完全可选且被 clamp——gemini_client=None、调用失败、响应畸形,都
  不影响事实性复盘本身落库(教训字段只是正文末尾追加的一句话,失败就留空)。
"""
import datetime as dt
import json
import logging

from sqlalchemy.orm import Session

from app.store.repos.decision_repo import get_decisions
from app.store.repos.memory_repo import add_entry, get_entries
from app.store.repos.paper_repo import get_fills

logger = logging.getLogger(__name__)

_MAX_LESSON_LEN = 150


def reconstruct_closed_trades(session: Session) -> list[dict]:
    """按标的、按(fill_date, id)顺序重放全部成交流水,用均价法(average-cost)
    确定性重建每一次卖出对应的已实现盈亏。纯函数式读取,不写库、不调用 LLM。

    返回按 sell_fill_id 升序排列的已平仓交易字典列表(无卖出 → [])。
    """
    fills = get_fills(session)
    by_symbol: dict[str, list] = {}
    for fill in fills:
        by_symbol.setdefault(fill.symbol, []).append(fill)

    closed: list[dict] = []
    for symbol, symbol_fills in by_symbol.items():
        symbol_fills.sort(key=lambda f: (f.fill_date, f.id))
        running_shares = 0
        running_cost = 0.0
        lot_open_date: dt.date | None = None
        for fill in symbol_fills:
            if fill.side == "buy":
                if running_shares == 0:
                    lot_open_date = fill.fill_date
                running_cost = (
                    (running_cost * running_shares + fill.shares * fill.price)
                    / (running_shares + fill.shares)
                )
                running_shares += fill.shares
            elif fill.side == "sell":
                buy_vwap = running_cost
                realized_pnl = fill.shares * (fill.price - buy_vwap)
                denom = buy_vwap * fill.shares
                realized_pnl_pct = (realized_pnl / denom * 100) if denom else 0.0
                holding_days = (fill.fill_date - lot_open_date).days if lot_open_date else None
                running_shares = max(0, running_shares - fill.shares)
                fully_closed = running_shares == 0
                if fully_closed:
                    lot_open_date = None
                closed.append({
                    "sell_fill_id": fill.id,
                    "symbol": symbol,
                    "shares": fill.shares,
                    "buy_vwap": buy_vwap,
                    "sell_price": fill.price,
                    "sell_date": fill.fill_date,
                    "realized_pnl": realized_pnl,
                    "realized_pnl_pct": realized_pnl_pct,
                    "holding_days": holding_days,
                    "fully_closed": fully_closed,
                })
    closed.sort(key=lambda t: t["sell_fill_id"])
    return closed


def _reviewed_sell_fill_ids(session: Session) -> set:
    """幂等键集合:已经写过 trade_review 的 sell_fill_id(从既有条目的
    evidence_json 里读回)。"""
    ids: set = set()
    for row in get_entries(session, kind="trade_review"):
        try:
            evidence = json.loads(row.evidence_json)
        except (TypeError, ValueError):
            continue
        sell_fill_id = evidence.get("sell_fill_id") if isinstance(evidence, dict) else None
        if sell_fill_id is not None:
            ids.add(sell_fill_id)
    return ids


def _chair_verdict(payload_json: str) -> str | None:
    try:
        payload = json.loads(payload_json)
    except (TypeError, ValueError):
        return None
    chair = payload.get("chair") if isinstance(payload, dict) else None
    if not isinstance(chair, dict):
        return None
    verdict = chair.get("verdict")
    return verdict.strip() if isinstance(verdict, str) and verdict.strip() else None


def _find_rationale(session: Session, symbol: str, action: str,
                    as_of: dt.date | None) -> str | None:
    """best-effort 拉某标的在某日的买/卖决定的 chair.verdict 作为理由;任何异常
    (查不到/解析失败)一律返回 None,由调用方省略对应句子——绝不让复盘因为
    找不到历史决定而失败。"""
    if as_of is None:
        return None
    try:
        decisions = get_decisions(session, as_of)
        for row in decisions:
            if row.symbol == symbol and row.action == action:
                verdict = _chair_verdict(row.payload_json)
                if verdict:
                    return verdict
    except Exception:
        logger.warning("reflection: rationale lookup failed for %s/%s@%s",
                       symbol, action, as_of, exc_info=True)
        return None
    return None


def _llm_lesson(gemini_client, trade: dict, buy_rationale: str | None,
                sell_rationale: str | None) -> str:
    """可选的一句话教训——只喂事实,不能改动任何数字。任何失败(无 client、
    调用异常、响应畸形/非 dict/无 lesson 字段)一律返回空字符串,事实性复盘
    照常落库。"""
    if gemini_client is None:
        return ""
    try:
        facts = {
            "symbol": trade["symbol"],
            "buy_vwap": trade["buy_vwap"],
            "sell_price": trade["sell_price"],
            "realized_pnl_pct": trade["realized_pnl_pct"],
            "holding_days": trade["holding_days"],
            "buy_rationale": buy_rationale or "",
            "sell_rationale": sell_rationale or "",
        }
        prompt = (
            "以下是一笔已平仓模拟股票交易的客观事实(仅供参考,不要改动、也不要"
            "复述任何数字):\n"
            f"{json.dumps(facts, ensure_ascii=False)}\n"
            '请给出一句简短的中文教训/观察,严格按 {"lesson": "..."} 的 JSON 格式返回。'
        )
        result = gemini_client.generate_json(prompt)
        if not isinstance(result, dict):
            return ""
        lesson = result.get("lesson")
        if not isinstance(lesson, str):
            return ""
        lesson = lesson.strip()
        if not lesson:
            return ""
        return lesson if len(lesson) <= _MAX_LESSON_LEN else lesson[: _MAX_LESSON_LEN - 1] + "…"
    except Exception:
        logger.warning("reflection: llm lesson failed (facts stand alone)", exc_info=True)
        return ""


def _format_body(trade: dict, buy_rationale: str | None, sell_rationale: str | None,
                 lesson: str) -> str:
    core = (
        f"{trade['symbol']} 平仓复盘:{trade['shares']} 股,"
        f"买入均价 {trade['buy_vwap']:.2f} → 卖出 {trade['sell_price']:.2f},"
        f"持有 {trade['holding_days']} 天,"
        f"已实现盈亏 {trade['realized_pnl']:+.0f}({trade['realized_pnl_pct']:+.1f}%)。"
    )
    parts = [core]
    if buy_rationale:
        parts.append(f"买入理由:{buy_rationale}。")
    if sell_rationale:
        parts.append(f"卖出理由:{sell_rationale}。")
    if lesson:
        parts.append(lesson)
    return "".join(parts)


def _entry_to_dict(row) -> dict:
    return {
        "id": row.id,
        "kind": row.kind,
        "title": row.title,
        "body": row.body,
        "symbol": row.symbol,
        "status": row.status,
        "evidence_json": row.evidence_json,
        "source": row.source,
        "created_at": row.created_at.isoformat(),
    }


def reflect_on_closed_trades(session: Session, gemini_client=None, *,
                             now: dt.datetime | None = None) -> list[dict]:
    """对所有尚未复盘的已平仓交易(以 sell_fill_id 去重)写入一条
    kind="trade_review" 的知识库条目;已经写过的平仓不会重复写(幂等)。

    `now` 目前未参与任何计算(平仓事实全部来自成交流水的 fill_date),仅为将来
    扩展(如"距今 N 天"提示语)预留、并与本仓库其它服务(watchdog/circuit_
    breaker/trade_cycle)统一的"服务端时钟可注入"约定保持一致。
    """
    closed = reconstruct_closed_trades(session)
    if not closed:
        return []

    reviewed_ids = _reviewed_sell_fill_ids(session)
    created: list[dict] = []
    for trade in closed:
        if trade["sell_fill_id"] in reviewed_ids:
            continue

        symbol = trade["symbol"]
        sell_date = trade["sell_date"]
        holding_days = trade["holding_days"]
        lot_open_date = (sell_date - dt.timedelta(days=holding_days)
                         if holding_days is not None else None)

        buy_rationale = _find_rationale(session, symbol, "buy", lot_open_date)
        sell_rationale = _find_rationale(session, symbol, "sell", sell_date)
        lesson = _llm_lesson(gemini_client, trade, buy_rationale, sell_rationale)
        body = _format_body(trade, buy_rationale, sell_rationale, lesson)
        title = f"{symbol} 平仓 {sell_date.isoformat()} {trade['realized_pnl_pct']:+.1f}%"
        evidence = {
            "sell_fill_id": trade["sell_fill_id"],
            "symbol": symbol,
            "realized_pnl": trade["realized_pnl"],
            "realized_pnl_pct": trade["realized_pnl_pct"],
            "holding_days": holding_days,
            "buy_vwap": trade["buy_vwap"],
            "sell_price": trade["sell_price"],
            "shares": trade["shares"],
        }
        row = add_entry(session, "trade_review", title, body, symbol=symbol,
                        status="active", evidence=evidence, source="reflection")
        created.append(_entry_to_dict(row))
    return created
