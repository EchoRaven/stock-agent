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

build_forward_returns 是这个模块的第二个测量:决策记分卡量的是推荐的"形状"
(动作分布、置信度分布),回答不了"这些推荐对不对"。这个函数把每条决策的
as_of 之后的实际股价接上去,按 action、按置信度分桶,看收益。核心诚实约束:
`pending`(horizon 还没到期)和 `unpriced`(抓不到行情)绝不能悄悄退化成 0 或
被当作"已测量但是零收益"——两者都单独计数,从不进入 matured 统计;
confidence_signal(置信度是否真的预测收益)在 matured 买入样本 >= MIN_SIGNAL_N
之前不给结论,防止小样本噪声被误读成"信号"。
"""
import bisect
import datetime as dt
import math
import statistics

from sqlalchemy.orm import Session

from app.services.market_data_service import fetch_bars
from app.store.repos.decision_repo import get_decisions_since
from app.store.repos.order_repo import STATUSES, count_orders_by_status
from app.util.trading_day import et_trading_day

MIN_FOR_FLAGS = 10
BUY_HEAVY_PCT_THRESHOLD = 0.70
NO_SELLS_PCT_THRESHOLD = 0.05
FLAT_CONFIDENCE_STDEV_THRESHOLD = 0.08
CONFIDENCE_FLOOR_THRESHOLD = 0.6

# forward-returns knobs
MIN_SIGNAL_N = 20  # matured BUY decisions needed before confidence_signal draws a conclusion
FORWARD_RETURN_LOOKBACK_BUFFER_DAYS = 10  # bar-fetch window pad before min(as_of)
CONFIDENCE_SIGNAL_POS_THRESHOLD = 0.15
CONFIDENCE_SIGNAL_NEG_THRESHOLD = -0.15
DEFAULT_HORIZONS = (1, 5, 20)

HIT_RATE_MEANING = {
    "buy": "涨了算对",
    "sell": "跌了算对,即避开了损失",
    "hold": "不作方向性判断,不计 hit_rate",
}

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


# ---------------------------------------------------------------------------
# forward returns — do the decisions pay off?
# ---------------------------------------------------------------------------

def _entry_lookup(bars_df, as_of: dt.date) -> tuple[int, float] | None:
    """Position + close of the last bar with date <= as_of. None (unpriced) if
    the symbol has no bars at all, or every bar is after as_of, or the close
    is non-positive (can't form a percentage return off it)."""
    if bars_df is None or bars_df.empty:
        return None
    dates = list(bars_df.index.date)
    pos = bisect.bisect_right(dates, as_of) - 1
    if pos < 0:
        return None
    price = float(bars_df["close"].iloc[pos])
    if price <= 0:
        return None
    return pos, price


def _action_stats(returns: list[float], action: str) -> dict:
    n = len(returns)
    if n == 0:
        return {
            "n": 0, "mean_return_pct": None, "median_return_pct": None,
            "hit_rate": None, "hit_rate_meaning": HIT_RATE_MEANING[action],
        }
    hit_rate: float | None
    if action == "buy":
        hit_rate = _round3(sum(1 for r in returns if r > 0) / n)
    elif action == "sell":
        hit_rate = _round3(sum(1 for r in returns if r < 0) / n)
    else:  # hold: no directional claim
        hit_rate = None
    return {
        "n": n,
        "mean_return_pct": _round3(statistics.mean(returns)),
        "median_return_pct": _round3(statistics.median(returns)),
        "hit_rate": hit_rate,
        "hit_rate_meaning": HIT_RATE_MEANING[action],
    }


def _buy_by_confidence(matured_buys: list[tuple[float, float]]) -> list[dict]:
    """matured_buys: [(confidence, return_pct), ...]. Every HIST_BUCKETS label
    is always listed (n==0 buckets get None stats), same bucket boundaries as
    the confidence histogram."""
    buckets: dict[str, list[float]] = {label: [] for label, _, _ in HIST_BUCKETS}
    for confidence, ret in matured_buys:
        for label, lo, hi in HIST_BUCKETS:
            if lo is not None and confidence < lo:
                continue
            if hi is not None and confidence >= hi:
                continue
            buckets[label].append(ret)
            break

    out = []
    for label, _, _ in HIST_BUCKETS:
        rets = buckets[label]
        n = len(rets)
        out.append({
            "bucket": label,
            "n": n,
            "mean_return_pct": _round3(statistics.mean(rets)) if n else None,
            "hit_rate": _round3(sum(1 for r in rets if r > 0) / n) if n else None,
        })
    return out


def _pearson_r(xs: list[float], ys: list[float]) -> float | None:
    """No-scipy Pearson correlation. None when undefined (n<2 or either series
    has zero variance)."""
    n = len(xs)
    if n < 2:
        return None
    mean_x = statistics.mean(xs)
    mean_y = statistics.mean(ys)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if den_x == 0 or den_y == 0:
        return None
    return num / (den_x * den_y)


def _signal_verdict(r: float) -> str:
    if r > CONFIDENCE_SIGNAL_POS_THRESHOLD:
        return f"置信度与收益正相关(r={r})——高置信度确实更好"
    if r < CONFIDENCE_SIGNAL_NEG_THRESHOLD:
        return f"负相关(r={r})——置信度反向,需重新校准"
    return f"≈无关(r={r})——置信度目前不带信息"


def _confidence_signal(matured_buys: list[tuple[float, float]]) -> dict:
    n = len(matured_buys)
    if n < MIN_SIGNAL_N:
        return {
            "n": n, "pearson_r": None, "verdict": None,
            "note": f"样本不足(需≥{MIN_SIGNAL_N}条已成熟买入决策),暂不下结论",
        }
    confidences = [c for c, _ in matured_buys]
    returns = [r for _, r in matured_buys]
    r = _pearson_r(confidences, returns)
    if r is None:
        return {
            "n": n, "pearson_r": None, "verdict": None,
            "note": "置信度或收益在样本内没有变化,无法计算相关系数",
        }
    r3 = _round3(r)
    return {"n": n, "pearson_r": r3, "verdict": _signal_verdict(r3)}


def _build_forward_returns_note(total: int, distinct_days: int,
                                horizons: tuple[int, ...], by_horizon: dict) -> str:
    if total == 0:
        return "暂无决策数据,记分卡将在产生决策后开始积累"

    matured_horizons = [h for h in horizons if by_horizon[str(h)]["coverage"]["matured"] > 0]
    day_desc = f"{distinct_days} 个交易日" if distinct_days != 1 else "1 个交易日"
    if not matured_horizons:
        return (f"{total} 条决策全部来自 {day_desc},目前没有任何 horizon 有成熟数据"
                "(全部 pending/unpriced)——暂不下结论")
    matured_desc = "、".join(f"{h}日" for h in matured_horizons)
    return f"{total} 条决策来自 {day_desc};已有成熟数据的 horizon:{matured_desc}"


def build_forward_returns(session: Session, price_provider,
                          horizons: tuple[int, ...] = DEFAULT_HORIZONS,
                          days: int | None = None,
                          now_utc: dt.datetime | None = None) -> dict:
    """决策是否奏效 —— 按 action、按置信度分桶量出前瞻收益(forward returns)。

    Mechanics per decision: entry = close on as_of, or the last close on/
    before as_of if as_of itself has no bar; if no such bar exists at all
    (symbol failed to fetch, came back empty, or every bar is after as_of)
    the decision is **unpriced** for every horizon. For horizon h, exit is
    the close of the h-th bar AFTER the entry bar, counted by bar position
    (not calendar days) — weekends/holidays don't skew the horizon. If fewer
    than h bars exist after the entry bar, the decision is **pending** for
    that horizon (not yet matured, NOT a zero). `pending` and `unpriced` are
    tracked separately from `matured` and never folded into the return
    statistics — an absent measurement must never look like a measured zero.

    hit_rate semantics differ by action, so each carries its own
    hit_rate_meaning string:
    - buy:  fraction of matured decisions with return_pct > 0 ("涨了算对").
    - sell: fraction of matured decisions with return_pct < 0 ("跌了算对,
      即避开了损失" — falling after a sell call means the call was right).
    - hold: hit_rate is always None — hold makes no directional claim, so
      there's no "right/wrong" to score.

    confidence_signal (does higher confidence actually predict better
    returns?) only draws a conclusion once matured BUY n >= MIN_SIGNAL_N
    (20); below that it returns pearson_r=None with a "样本不足" note so a
    handful of noisy decisions can't masquerade as a calibration signal.

    Never raises on an empty DB or a price provider that fails/returns
    nothing for every symbol.
    """
    now = now_utc or dt.datetime.now(dt.UTC)
    today = et_trading_day(now)
    since = None
    if days is not None:
        since = today - dt.timedelta(days=days)

    rows = get_decisions_since(session, since=since)
    horizons = tuple(horizons)

    total_decisions = len(rows)
    distinct_symbols = len({r.symbol for r in rows})
    distinct_days = len({r.as_of for r in rows})
    as_of_values = [r.as_of for r in rows]
    as_of_from = min(as_of_values).isoformat() if as_of_values else None
    as_of_to = max(as_of_values).isoformat() if as_of_values else None

    # entry lookup is a per-decision, horizon-independent property computed once.
    entries: dict[int, tuple[int, float, object] | None] = {}
    if rows:
        symbols = sorted({r.symbol for r in rows})
        start = min(as_of_values) - dt.timedelta(days=FORWARD_RETURN_LOOKBACK_BUFFER_DAYS)
        bars_by_symbol, _skipped = fetch_bars(price_provider, symbols, start, today)
        for row in rows:
            bars_df = bars_by_symbol.get(row.symbol)
            found = _entry_lookup(bars_df, row.as_of)
            entries[row.id] = (found[0], found[1], bars_df) if found else None

    by_horizon: dict[str, dict] = {}
    for h in horizons:
        matured = pending = unpriced = 0
        returns_by_action: dict[str, list[float]] = {a: [] for a in ACTIONS}
        matured_buys: list[tuple[float, float]] = []

        for row in rows:
            entry = entries.get(row.id)
            if entry is None:
                unpriced += 1
                continue
            pos, entry_price, bars_df = entry
            exit_pos = pos + h
            if exit_pos >= len(bars_df):
                pending += 1
                continue
            exit_price = float(bars_df["close"].iloc[exit_pos])
            ret = _round3((exit_price / entry_price - 1) * 100)
            matured += 1
            if row.action in returns_by_action:
                returns_by_action[row.action].append(ret)
            if row.action == "buy":
                matured_buys.append((row.confidence, ret))

        by_horizon[str(h)] = {
            "coverage": {"matured": matured, "pending": pending, "unpriced": unpriced},
            "by_action": {a: _action_stats(returns_by_action[a], a) for a in ACTIONS},
            "buy_by_confidence": _buy_by_confidence(matured_buys),
            "confidence_signal": _confidence_signal(matured_buys),
        }

    note = _build_forward_returns_note(total_decisions, distinct_days, horizons, by_horizon)

    return {
        "total_decisions": total_decisions,
        "distinct_symbols": distinct_symbols,
        "as_of_from": as_of_from,
        "as_of_to": as_of_to,
        "horizons": list(horizons),
        "by_horizon": by_horizon,
        "note": note,
    }
