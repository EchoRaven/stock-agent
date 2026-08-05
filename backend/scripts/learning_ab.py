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


def _run_condition(gemini, briefing, memory_context, reps):
    """同一材料上跑 reps 次,返回 (action 列表, 平均置信度, 平均买入倾向分)。"""
    actions, confs, buys = [], [], []
    for _ in range(reps):
        r = run_committee(gemini, briefing, held=False, memory_context=memory_context)
        actions.append(r["action"])
        confs.append(r["confidence"])
        # 买入倾向 = sign(action) × confidence;越低越不敢买
        buys.append(_ACTION_SIGN.get(r["action"], 0.0) * r["confidence"])
    return actions, round(statistics.mean(confs), 3), round(statistics.mean(buys), 3)


def _eval_symbol(gemini, session, sym, providers, as_of, reps):
    price, news, funds = providers
    mem = get_committee_context(session, sym)
    briefing = get_stock_briefing(sym, price, news, funds, as_of)
    wa, wc, wb = _run_condition(gemini, briefing, mem, reps)
    na, nc, nb = _run_condition(gemini, briefing, "", reps)
    print(f"\n  {sym:6s} (memory {len(mem)} 字)")
    print(f"    WITH 记忆 : action {wa} 置信度均 {wc}  买入倾向 {wb:+.3f}")
    print(f"    WITHOUT   : action {na} 置信度均 {nc}  买入倾向 {nb:+.3f}")
    print(f"    Δ买入倾向(WITH-WITHOUT) = {wb - nb:+.3f}")
    return wb - nb  # 负 = 有记忆后更不敢买


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default="scripts/replay_loop.db", help="replay_loop 生成的独立库")
    ap.add_argument("--as-of", default="2025-04-07", help="评估日(复盘之后的某个交易日)")
    ap.add_argument("--reps", type=int, default=3, help="每个条件重复次数(压随机性)")
    ap.add_argument("--controls", type=int, default=2, help="对照标的数(池中无复盘的票)")
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
        treatment = sorted({e.symbol for e in get_entries(session, kind="trade_review") if e.symbol})
        if not treatment:
            print("库里没有 trade_review 标的,先跑 replay_loop 产生复盘。", file=sys.stderr)
            return 0
        controls = [s for s in DEFAULT_UNIVERSE if s not in treatment][:args.controls]

        print(f"评估日 {as_of} · 每条件 {args.reps} 次 · "
              f"treatment(有复盘) {treatment} · control(无复盘) {controls}")

        print("\n【treatment:有自己交易复盘的标的】")
        t_deltas = [_eval_symbol(gemini, session, s, providers, as_of, args.reps) for s in treatment]
        print("\n【control:池内无复盘的标的】")
        c_deltas = [_eval_symbol(gemini, session, s, providers, as_of, args.reps) for s in controls]

        t_mean = round(statistics.mean(t_deltas), 3) if t_deltas else None
        c_mean = round(statistics.mean(c_deltas), 3) if c_deltas else None
        print("\n" + "=" * 68)
        print("difference-in-differences(隔离复盘因果效应)")
        print("=" * 68)
        print(f"  treatment 平均 Δ买入倾向 = {t_mean}  (有复盘的票,读记忆前后)")
        print(f"  control   平均 Δ买入倾向 = {c_mean}  (无复盘的票,读记忆前后)")
        if t_mean is not None and c_mean is not None:
            did = round(t_mean - c_mean, 3)
            print(f"  DiD = treatment - control = {did:+.3f}")
            if did < -0.1:
                verdict = "复盘让委员会对该票明显更谨慎 → 学习效应初步可见(弱证据)"
            elif did > 0.1:
                verdict = "反常:有复盘反而更敢买 → 需查复盘是否被正确读取/框定"
            else:
                verdict = "无明显差异 → 闭环通电但委员会对自己的复盘几乎无响应(这才是真问题)"
            print(f"  读数: {verdict}")
        print("\n  ⚠ 样本极小(N复盘票少、reps少、单一评估日)——机制探针,非结论。"
              "\n  ⚠ WITH 条件含通用播种知识,DiD 用 control 扣除其影响,但残留噪声仍大。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
