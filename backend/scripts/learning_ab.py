"""M8 学习效应 A/B —— 委员会读到自己的复盘后,行为是否真改变?

replay_loop 证明了闭环"通电"(平仓 → trade_review 写进记忆),但没回答关键问题:
**委员会读到自己的复盘后,决策是否真的改变?** 通电不等于学到。

受控实验(difference-in-differences,隔离复盘的因果效应):
  条件 WITH   : memory_context = get_committee_context(症状票)  —— 含该票复盘
  条件 WITHOUT: memory_context = ""                              —— 无任何记忆
在**同一份材料(briefing)**上各跑 N 次(压 LLM 随机性),比较 action / 置信度。

只看 WITH-vs-WITHOUT 会把"复盘的效应"和"通用播种知识的效应"混在一起。所以同时测:
  - 有复盘的标的(treatment):JPM/AMD 等
  - 无复盘的对照标的(control):同池但没被平仓过的票
若 treatment 的置信度降幅**显著大于** control,才是复盘在起作用(而非通用知识)。

读 replay_loop 生成的独立库(--db),绝不碰线上库。NOT part of pytest。需 Gemini key。

Usage:
    uv run python -m scripts.learning_ab --db scripts/replay_loop.db --as-of 2025-04-07
"""

import argparse
import datetime as dt
import math
import statistics
import sys

from app.config import get_settings
from app.data.cache import CachedPriceProvider
from app.data.fundamentals_edgar import EdgarFundamentalsProvider
from app.data.news_factory import build_news_provider
from app.data.prices_yfinance import YFinancePriceProvider
from app.llm.gemini import GeminiClient
from app.screener.universe import DEFAULT_UNIVERSE
from app.services.briefing_service import get_stock_briefing
from app.services.committee_service import run_committee
from app.services.memory_service import get_committee_context
from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.memory_repo import get_entries
from scripts._health import GeminiUnavailable, require_gemini

# 置信度做 buy 归一化:hold/sell 视为"不买",便于横比"读复盘后是否更不敢买"
_ACTION_SIGN = {"buy": 1.0, "hold": 0.0, "sell": -1.0}


def _wilson(k: int, n: int, z: float = 1.96):
    """二项比例的 Wilson 95% 置信区间。小样本上比正态近似稳。返回 (p, lo, hi)。
    跨项目教训(见 Idea/_INDEX):小 n 上的漂亮点估计最容易被当成硬结论,必须配区间。"""
    if n == 0:
        return (0.0, 0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    center = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(p, 3), round(max(0.0, center - half), 3), round(min(1.0, center + half), 3))


def _run_condition(gemini, briefing, memory_context, reps):
    """同一材料上跑 reps 次,返回 (action 列表, 平均置信度, 买入次数)。
    买入次数是主指标(比例),配 Wilson 区间;买入倾向均值仅作辅参。"""
    actions, confs, buy_count = [], [], 0
    for _ in range(reps):
        r = run_committee(gemini, briefing, held=False, memory_context=memory_context)
        actions.append(r["action"])
        confs.append(r["confidence"])
        if r["action"] == "buy":
            buy_count += 1
    return actions, round(statistics.mean(confs), 3), buy_count


def _eval_symbol(gemini, session, sym, providers, as_of, reps):
    """返回 (with_buys, without_buys):该票在 WITH/WITHOUT 记忆两条件下的买入次数。"""
    price, news, funds = providers
    mem = get_committee_context(session, sym)
    briefing = get_stock_briefing(sym, price, news, funds, as_of)
    wa, wc, wbuys = _run_condition(gemini, briefing, mem, reps)
    na, nc, nbuys = _run_condition(gemini, briefing, "", reps)
    print(f"\n  {sym:6s} (memory {len(mem)} 字)  买入次数 WITH {wbuys}/{reps} · "
          f"WITHOUT {nbuys}/{reps}")
    return wbuys, nbuys


def _inject_synthetic_review(session, symbol, idx):
    """给 symbol 注入一条与 reflect_on_closed_trades 同格式的**合成亏损复盘**,
    让 learning_ab 能把 treatment 样本便宜地扩到 8-10 只(不用真跑那么多交易)。
    亏损幅度按 idx 递变(-4% ~ -18%),更真实。格式严格对齐真实复盘,get_committee_
    context 读起来与真的无差。"""
    from app.store.repos.memory_repo import add_entry
    loss_pct = -(4.0 + 2.0 * idx)          # -4, -6, -8, ...
    loss_pct = max(loss_pct, -18.0)
    pnl = loss_pct / 100.0 * 12000          # 假一个美元亏损额,量级合理即可
    title = f"{symbol} 平仓 2025-03-28 {loss_pct:+.1f}%"
    body = (f"已实现盈亏 {pnl:+.0f}({loss_pct:+.1f}%)。买入理由:动量走强、看似向上突破;"
            f"卖出理由:追高后回落、买入理由未兑现。教训:在 {symbol} 上追强势买入,"
            f"随后的回撤把浮盈和部分本金一起吃掉了——同样的形态下次要额外小心。")
    add_entry(session, "trade_review", title, body, symbol=symbol,
              evidence={"sell_fill_id": 900000 + idx, "realized_pnl_pct": loss_pct},
              source="synthetic")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default="scripts/replay_loop.db", help="replay_loop 生成的独立库")
    ap.add_argument("--as-of", default="2025-04-07", help="评估日(复盘之后的某个交易日)")
    ap.add_argument("--reps", type=int, default=3, help="每个条件重复次数(压随机性)")
    ap.add_argument("--controls", type=int, default=2, help="对照标的数(池中无复盘的票)")
    ap.add_argument("--inject", default="",
                    help="逗号分隔的标的,注入合成亏损复盘作 treatment(加功率用;"
                         "建议指向一份 replay_loop.db 的**副本**,别污染原库)")
    args = ap.parse_args(argv)

    settings = get_settings()
    if not settings.gemini_api_key:
        print("没有 STOCKAGENT_GEMINI_API_KEY", file=sys.stderr)
        return 2
    providers = (CachedPriceProvider(YFinancePriceProvider(), settings.cache_dir),
                 build_news_provider(settings),
                 EdgarFundamentalsProvider(settings.edgar_user_agent))
    gemini = GeminiClient()
    try:
        require_gemini(gemini)
    except GeminiUnavailable as exc:
        print(f"⛔ {exc}", file=sys.stderr)
        return 3

    as_of = dt.date.fromisoformat(args.as_of)
    engine = make_engine(args.db)
    init_db(engine)
    with make_session_factory(engine)() as session:
        inject = [s.strip().upper() for s in args.inject.split(",") if s.strip()]
        if inject:
            existing = {e.symbol for e in get_entries(session, kind="trade_review")}
            n = 0
            for i, sym in enumerate(inject):
                if sym in existing:
                    continue  # 已有真实复盘的不重复注入
                _inject_synthetic_review(session, sym, i)
                n += 1
            session.commit()
            print(f"已注入 {n} 条合成亏损复盘作 treatment(源=synthetic)")
        treatment = sorted({e.symbol for e in get_entries(session, kind="trade_review") if e.symbol})
        if not treatment:
            print("库里没有 trade_review 标的,先跑 replay_loop 产生复盘。", file=sys.stderr)
            return 0
        controls = [s for s in DEFAULT_UNIVERSE if s not in treatment][:args.controls]

        print(f"评估日 {as_of} · 每条件 {args.reps} 次 · "
              f"treatment(有复盘) {treatment} · control(无复盘) {controls}")

        print("\n【treatment:有自己交易复盘的标的】")
        t = [_eval_symbol(gemini, session, s, providers, as_of, args.reps) for s in treatment]
        print("\n【control:池内无复盘的标的】")
        c = [_eval_symbol(gemini, session, s, providers, as_of, args.reps) for s in controls]

        def _pool(rows):  # rows: [(with_buys, without_buys), ...] → 汇总买入次数
            n = len(rows) * args.reps
            return sum(r[0] for r in rows), sum(r[1] for r in rows), n

        tw, two, tn = _pool(t)
        cw, cwo, cn = _pool(c)
        print("\n" + "=" * 72)
        print("买入率(比例 + Wilson 95% 区间)—— 复盘是否压低了买入")
        print("=" * 72)

        def _line(label, k, n):
            p, lo, hi = _wilson(k, n)
            print(f"  {label:22s} 买入 {k:3d}/{n:<3d} = {p:.2f}  [95%CI {lo:.2f}, {hi:.2f}]")
            return p, lo, hi

        twp = _line("treatment · WITH记忆", tw, tn)
        twop = _line("treatment · WITHOUT ", two, tn)
        cwp = _line("control   · WITH记忆", cw, cn)
        cwop = _line("control   · WITHOUT ", cwo, cn)

        t_drop = round(twp[0] - twop[0], 3)   # 负 = WITH 记忆后买得更少
        c_drop = round(cwp[0] - cwop[0], 3)
        did = round(t_drop - c_drop, 3)
        print(f"\n  treatment 买入率变化(WITH−WITHOUT) = {t_drop:+.3f}")
        print(f"  control   买入率变化(WITH−WITHOUT) = {c_drop:+.3f}")
        print(f"  DiD = {did:+.3f}")
        # 诚实判读:只有当 treatment 的 WITH 区间与 WITHOUT 区间**分离**、且比 control
        # 降得更多,才算复盘真的压低了买入。区间重叠 = 测不出效应。
        separated = twp[2] < twop[1] or twp[1] > twop[2]  # treatment WITH vs WITHOUT 区间不重叠
        if separated and did < 0:
            verdict = "treatment 的 WITH/WITHOUT 区间分离且 DiD<0 → 复盘确实压低了买入(初步证据)"
        else:
            verdict = ("treatment 的 WITH/WITHOUT 区间**重叠** → 在此样本下测不出复盘对买入的影响"
                       "(不是'确证无效',是'区间没分开')")
        print(f"  读数: {verdict}")
        print(f"\n  ⚠ 单一评估日({as_of})、reps={args.reps};区间已如实反映不确定性。"
              "\n  ⚠ 合成复盘(注入)与真实复盘格式一致但非真实交易;treatment 含 2 只真实(AMD/JPM)。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
