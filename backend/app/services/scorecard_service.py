"""决策记分卡 —— 委员会推荐是否有区分度的纯聚合测量。

背景:实盘 46 条决策观察到 买入 78%(36 buy / 9 hold / 1 sell)、置信度压缩在
0.65–0.90(仅 7 个不同值,从未给出低置信度)——"buy, 0.85" 说了 46 遍,几乎没有
区分度。在改委员会 prompt 之前,先把这个偏差量出来。

纯函数,只读聚合:不调用 LLM、不发起任何网络请求、不写库。gate 字段读
OrderRow 状态计数,用来看 RiskGate 实际挡下了多少委员会的下单意图——这本身
也是判断"委员会说了算不算数"的一部分。

flags 是这个端点的重点:total < MIN_FOR_FLAGS 时样本太小,直接给
insufficient_data,不做任何校准判断(避免小样本噪声被误读成"委员会有偏差");
达到门槛后按固定阈值检测偏买/不卖/置信度扁平/置信度下限压缩,全部为阴性时给
calibration_ok——绝不留空列表(空列表会被前端误当作"还没查到数据"而不是"数据
很健康")。
"""
import datetime as dt
import statistics

from sqlalchemy.orm import Session

from app.store.repos.decision_repo import get_decisions_since
from app.store.repos.order_repo import STATUSES, count_orders_by_status
from app.util.trading_day import et_trading_day

MIN_FOR_FLAGS = 10
BUY_HEAVY_PCT_THRESHOLD = 0.70
NO_SELLS_PCT_THRESHOLD = 0.05
FLAT_CONFIDENCE_STDEV_THRESHOLD = 0.08
CONFIDENCE_FLOOR_THRESHOLD = 0.6

ACTIONS = ("buy", "sell", "hold")

# (bucket label, lower bound inclusive or None, upper bound exclusive or None)
HIST_BUCKETS = (
    ("<0.5", None, 0.5),
    ("0.5–0.6", 0.5, 0.6),
    ("0.6–0.7", 0.6, 0.7),
    ("0.7–0.8", 0.7, 0.8),
    ("0.8–0.9", 0.8, 0.9),
    ("0.9–1.0", 0.9, None),
)


def _round3(x: float | None) -> float | None:
    return round(x, 3) if x is not None else None


def _confidence_stats(confidences: list[float]) -> dict:
    n = len(confidences)
    if n == 0:
        return {"n": 0, "mean": None, "median": None, "min": None, "max": None, "stdev": None}
    stdev = statistics.stdev(confidences) if n > 1 else 0.0
    return {
        "n": n,
        "mean": _round3(statistics.mean(confidences)),
        "median": _round3(statistics.median(confidences)),
        "min": _round3(min(confidences)),
        "max": _round3(max(confidences)),
        "stdev": _round3(stdev),
    }


def _histogram(confidences: list[float]) -> list[dict]:
    counts = {label: 0 for label, _, _ in HIST_BUCKETS}
    for c in confidences:
        for label, lo, hi in HIST_BUCKETS:
            if lo is not None and c < lo:
                continue
            if hi is not None and c >= hi:
                continue
            counts[label] += 1
            break
    return [{"bucket": label, "count": counts[label]} for label, _, _ in HIST_BUCKETS]


def _build_flags(total: int, by_action: dict, by_action_pct: dict, confidence: dict) -> list[dict]:
    if total < MIN_FOR_FLAGS:
        return [{
            "code": "insufficient_data",
            "severity": "info",
            "message": f"决策样本不足({total} 条,至少需要 {MIN_FOR_FLAGS} 条)——暂不做校准判断",
        }]

    flags: list[dict] = []

    buy_pct = by_action_pct["buy"]
    if buy_pct > BUY_HEAVY_PCT_THRESHOLD:
        flags.append({
            "code": "buy_heavy",
            "severity": "warn",
            "message": f"买入占比 {buy_pct * 100:.1f}% 偏高——推荐缺少区分度",
        })

    sell_pct = by_action_pct["sell"]
    if by_action["sell"] == 0 or sell_pct < NO_SELLS_PCT_THRESHOLD:
        flags.append({
            "code": "no_sells",
            "severity": "warn",
            "message": "几乎不给卖出建议",
        })

    stdev = confidence["stdev"]
    if stdev is not None and stdev < FLAT_CONFIDENCE_STDEV_THRESHOLD:
        flags.append({
            "code": "flat_confidence",
            "severity": "warn",
            "message": f"置信度区分度低(标准差 {stdev})——高低置信度无法区分",
        })

    cmin = confidence["min"]
    if cmin is not None and cmin >= CONFIDENCE_FLOOR_THRESHOLD:
        flags.append({
            "code": "confidence_floor",
            "severity": "info",
            "message": f"从未给出低置信度(最低 {cmin}),疑似区间被压缩",
        })

    if not flags:
        flags.append({
            "code": "calibration_ok",
            "severity": "info",
            "message": "决策分布与置信度暂无明显偏差",
        })

    return flags


def build_scorecard(session: Session, days: int | None = None,
                    now_utc: dt.datetime | None = None) -> dict:
    since = None
    if days is not None:
        now = now_utc or dt.datetime.now(dt.UTC)
        since = et_trading_day(now) - dt.timedelta(days=days)

    rows = get_decisions_since(session, since=since)

    total = len(rows)
    distinct_symbols = len({r.symbol for r in rows})
    as_of_values = [r.as_of for r in rows]
    as_of_from = min(as_of_values).isoformat() if as_of_values else None
    as_of_to = max(as_of_values).isoformat() if as_of_values else None

    by_action = {action: 0 for action in ACTIONS}
    by_mode: dict[str, int] = {}
    confidences: list[float] = []
    for row in rows:
        if row.action in by_action:
            by_action[row.action] += 1
        by_mode[row.mode] = by_mode.get(row.mode, 0) + 1
        confidences.append(row.confidence)

    by_action_pct = {
        action: (round(count / total, 3) if total else 0.0)
        for action, count in by_action.items()
    }

    confidence = _confidence_stats(confidences)
    histogram = _histogram(confidences)

    status_counts = count_orders_by_status(session)
    gate = {status: status_counts.get(status, 0) for status in STATUSES}

    flags = _build_flags(total, by_action, by_action_pct, confidence)

    return {
        "total": total,
        "distinct_symbols": distinct_symbols,
        "window_days": days,
        "as_of_from": as_of_from,
        "as_of_to": as_of_to,
        "by_action": by_action,
        "by_action_pct": by_action_pct,
        "confidence": confidence,
        "histogram": histogram,
        "by_mode": by_mode,
        "gate": gate,
        "flags": flags,
    }
