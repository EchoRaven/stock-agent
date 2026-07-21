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
confidence_signal(置信度是否真的预测收益)要同时满足 matured 买入样本
>= MIN_SIGNAL_N **且** 覆盖 >= MIN_SIGNAL_DAYS 个不同决策日才给结论:只数条数
会被"同一天几十只标的"骗过去(同天标的一起随大盘走,不是独立样本),防止小样本
或截面相关的噪声被误读成"信号"。
"""
import bisect
import datetime as dt
import math
import statistics
from collections import Counter

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
# ...and they must span at least this many distinct decision days. Counting rows
# alone is not enough: N buys made on the SAME day across N symbols all ride that
# day's market move, so they are cross-sectionally correlated, not N independent
# observations — a correlation computed off them can look real when it is just
# "the market went up that day". Real finding from 2026-07-21: 36 matured buys
# from a single day passed the row-count gate and produced r=0.112, which should
# never have been reported as a conclusion.
MIN_SIGNAL_DAYS = 5

# Two-tailed 5% critical t values by degrees of freedom; df > 30 -> normal 1.96.
# Used to decide whether an observed correlation is distinguishable from noise.
T_CRIT_05 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
    8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145,
    15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080,
    22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048,
    29: 2.045, 30: 2.042,
}
T_CRIT_05_LARGE_DF = 1.96
# A correlation is only called real if it survives a test whose sample size is
# the number of DECISION DAYS, not the number of rows. Same-day buys ride the
# same market move, so rows massively overstate the evidence: the 2026-07-21
# replay had r=0.257 over 58 rows (t=1.99, nominally borderline) but only 12
# days -> t=0.84, nowhere near significant. Judging by rows would have shipped
# "high confidence really is better" off ~12 independent observations.
DOMINANT_CONFIDENCE_SHARE_THRESHOLD = 0.6
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


def _build_no_sell_flags(by_action: dict, by_action_pct: dict, total: int,
                         held_coverage: dict) -> list[dict]:
    """no_sells 的分母必须是"持仓决策",不是全部决策——sell 只有 held=True
    时才结构上可能(committee_service._clamp_action 会把 held=False 时的 sell
    改写成 hold)。买入侧决策结构上不可能是 sell,混进分母会系统性低估卖出率。

    三条分支:
    - held_n > 0:按持仓决策判定,消息里点名分母(如 "3/48")。
    - held_n == 0 且**全部**是旧数据(held 全未知)→ 没有持仓信息可用,退回
      旧的全量分母行为,但消息里点名是旧数据,不能悄悄冒充新口径。
    - held_n == 0 但**不是**全部旧数据(即存在明确 held=False 的行)→ 卖出
      结构性不可能,给 sell_untestable(info),绝不给 no_sells——这正是
      2026-07-21 那次"3/62=4.8% 看起来像委员会不肯卖"的假象来源:诚实分母
      其实是 3/48=6.25%,而当 held 分母为 0 时更是连"不肯卖"这个判断本身
      都无法成立。
    """
    held_n = held_coverage["held"]
    not_held_n = held_coverage["not_held"]
    unknown_n = held_coverage["unknown"]

    if held_n > 0:
        sells = by_action["sell"]
        sell_rate = sells / held_n
        if sells == 0 or sell_rate < NO_SELLS_PCT_THRESHOLD:
            return [{
                "code": "no_sells",
                "severity": "warn",
                "message": f"持仓决策里几乎不给卖出建议({sells}/{held_n})",
            }]
        return []

    if unknown_n == total:
        # 没有任何一行带持仓标记(纯旧数据)——没法按持仓算,退回旧的全量
        # 分母,但要说明白这是旧数据。
        sell_pct = by_action_pct["sell"]
        if by_action["sell"] == 0 or sell_pct < NO_SELLS_PCT_THRESHOLD:
            return [{
                "code": "no_sells",
                "severity": "warn",
                "message": "几乎不给卖出建议(按全部决策计,旧数据无持仓标记)",
            }]
        return []

    # held_n == 0 且存在明确的 not_held 行:这个窗口里一次都没持仓过,卖出
    # 在结构上不可能发生——这不是"委员会不肯卖",是"根本没机会卖"。
    return [{
        "code": "sell_untestable",
        "severity": "info",
        "message": (f"本窗口没有出现持仓状态下的决策(held={held_n}/"
                    f"not_held={not_held_n}),卖出在结构上不可能发生,"
                    "卖出行为本期无法测量——不代表委员会不肯卖"),
    }]


def _build_flags(total: int, by_action: dict, by_action_pct: dict, confidence: dict,
                 held_coverage: dict) -> list[dict]:
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

    flags.extend(_build_no_sell_flags(by_action, by_action_pct, total, held_coverage))

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
    held_n = not_held_n = unknown_n = 0
    for row in rows:
        if row.action in by_action:
            by_action[row.action] += 1
        by_mode[row.mode] = by_mode.get(row.mode, 0) + 1
        confidences.append(row.confidence)
        if row.held is True:
            held_n += 1
        elif row.held is False:
            not_held_n += 1
        else:
            unknown_n += 1

    by_action_pct = {
        action: (round(count / total, 3) if total else 0.0)
        for action, count in by_action.items()
    }

    held_coverage = {"held": held_n, "not_held": not_held_n, "unknown": unknown_n}
    sell_rate_among_held = round(by_action["sell"] / held_n, 3) if held_n else None
    buy_rate_among_not_held = (round(by_action["buy"] / not_held_n, 3)
                               if not_held_n else None)

    confidence = _confidence_stats(confidences)
    histogram = _histogram(confidences)

    status_counts = count_orders_by_status(session)
    gate = {status: status_counts.get(status, 0) for status in STATUSES}

    flags = _build_flags(total, by_action, by_action_pct, confidence, held_coverage)

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
        "held_coverage": held_coverage,
        "sell_rate_among_held": sell_rate_among_held,
        "buy_rate_among_not_held": buy_rate_among_not_held,
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


def _buy_by_confidence(matured_buys: list[tuple[float, float, dt.date]]) -> list[dict]:
    """matured_buys: [(confidence, return_pct, as_of), ...]. Every HIST_BUCKETS
    label is always listed (n==0 buckets get None stats), same bucket boundaries
    as the confidence histogram."""
    buckets: dict[str, list[float]] = {label: [] for label, _, _ in HIST_BUCKETS}
    for confidence, ret, _as_of in matured_buys:
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


def _t_critical(df: int) -> float | None:
    """双尾 5% 临界 t 值;df < 1 无法检验 → None。"""
    if df < 1:
        return None
    return T_CRIT_05.get(df, T_CRIT_05_LARGE_DF)


def _t_statistic(r: float, df: int) -> float | None:
    """|t| = |r|·sqrt(df)/sqrt(1-r²)。df<1 → None;|r|>=1(完全相关)→ inf。"""
    if df < 1:
        return None
    if abs(r) >= 1.0:
        return math.inf
    return abs(r) * math.sqrt(df) / math.sqrt(1 - r * r)


def _signal_verdict(r: float, significant: bool, distinct_days: int) -> str:
    """先说显著性再说方向 —— 不显著时绝不能读成"高置信度更好"。"""
    direction = ("正" if r > CONFIDENCE_SIGNAL_POS_THRESHOLD
                 else "负" if r < CONFIDENCE_SIGNAL_NEG_THRESHOLD else "≈无")
    if not significant:
        return (f"方向为{direction}相关(r={r}),但**不显著**:按 {distinct_days} 个决策日"
                f"(而非行数)做保守检验后,与噪声无法区分,不能据此调整校准")
    if r > CONFIDENCE_SIGNAL_POS_THRESHOLD:
        return f"置信度与收益正相关(r={r})且通过按天保守检验——高置信度确实更好"
    if r < CONFIDENCE_SIGNAL_NEG_THRESHOLD:
        return f"负相关(r={r})且显著——置信度反向,需重新校准"
    return f"≈无关(r={r})——置信度目前不带信息"


def _confidence_signal(matured_buys: list[tuple[float, float, dt.date]]) -> dict:
    """置信度到底预不预测收益 —— 两道门都过了才下结论。

    门① 条数 >= MIN_SIGNAL_N;门② 覆盖的**不同决策日** >= MIN_SIGNAL_DAYS。
    只看条数会被"同一天几十只标的"骗:那天大盘涨,几乎所有标的都涨,几十个
    "样本"其实是一个观测。两道门任一不过 → pearson_r/verdict 都是 None + note
    说明为什么不下结论(而不是给一个看起来像结论的数)。
    """
    n = len(matured_buys)
    distinct_days = len({as_of for _, _, as_of in matured_buys})
    base = {"n": n, "distinct_days": distinct_days}
    if n < MIN_SIGNAL_N:
        return {
            **base, "pearson_r": None, "verdict": None,
            "note": f"样本不足(需≥{MIN_SIGNAL_N}条已成熟买入决策),暂不下结论",
        }
    if distinct_days < MIN_SIGNAL_DAYS:
        return {
            **base, "pearson_r": None, "verdict": None,
            "note": (f"决策只覆盖 {distinct_days} 个交易日(需≥{MIN_SIGNAL_DAYS} 天):"
                     f"同一天的多只标的会一起随大盘涨跌,不是相互独立的样本,"
                     f"条数再多也不能据此判断置信度是否有效"),
        }
    confidences = [c for c, _, _ in matured_buys]
    returns = [r for _, r, _ in matured_buys]
    r = _pearson_r(confidences, returns)
    if r is None:
        return {
            **base, "pearson_r": None, "verdict": None,
            "note": "置信度或收益在样本内没有变化,无法计算相关系数",
        }
    r3 = _round3(r)

    # 显著性按**天数**检验,不按行数(见 T_CRIT_05 注释):同一天的多条决策
    # 不是独立观测,用行数会把噪声判成信号。
    df = distinct_days - 2
    t_crit = _t_critical(df)
    t_stat = _t_statistic(r, df)
    significant = t_stat is not None and t_crit is not None and t_stat > t_crit

    # 置信度取值高度集中时,"相关性"实际上由少数几条偏离值决定,不是真的梯度。
    counts = Counter(confidences)
    dominant_share = _round3(counts.most_common(1)[0][1] / n)

    out = {
        **base,
        "pearson_r": r3,
        "t_stat": _round3(t_stat) if t_stat is not None else None,
        "t_critical": t_crit,
        "significant": significant,
        "dominant_confidence_share": dominant_share,
        "verdict": _signal_verdict(r3, significant, distinct_days),
    }
    if dominant_share >= DOMINANT_CONFIDENCE_SHARE_THRESHOLD:
        out["caveat"] = (f"{dominant_share:.0%} 的买入置信度都是同一个值,"
                         f"所谓相关性其实由少数几条偏离值决定,不是真实梯度")
    return out


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
    (20) AND those buys span >= MIN_SIGNAL_DAYS (5) distinct decision days;
    otherwise it returns pearson_r=None with a note explaining which gate
    failed, so neither a handful of noisy decisions nor a pile of same-day
    (cross-sectionally correlated) ones can masquerade as a calibration signal.

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
        matured_buys: list[tuple[float, float, dt.date]] = []

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
                matured_buys.append((row.confidence, ret, row.as_of))

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
