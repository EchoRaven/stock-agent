"""Historical replay evaluation of the committee — does its confidence predict returns?

Replays the live decision pipeline (screen -> briefing -> committee) over N past
trading days, then scores the resulting decisions with the forward-return
scorecard (app/services/scorecard_service.py).

WHY THIS EXISTS: the live decision record spans a single day, and
confidence_signal deliberately refuses to draw conclusions from same-day
decisions — N buys made on one day across N symbols all ride that day's market
move, so they are one observation, not N. Replaying past dates is the only way
to get multi-day decision data without waiting weeks of wall-clock time.

ISOLATION (important): writes to a SEPARATE SQLite file (default
scripts/replay_eval.db), never the live stockagent.db — synthetic decisions must
never contaminate the real decision record. This script never touches the
order/gate path: it calls run_committee directly and only ever writes rows with
mode="replay". No orders are created, nothing is submitted, no money path runs.

KNOWN BIASES — read before trusting any number this prints:
  * Fundamentals are NOT point-in-time. EdgarFundamentalsProvider.get_fundamentals(symbol)
    takes no as_of, so every replayed date sees TODAY's filings. Over a few weeks
    this is small (filings are quarterly) but it is genuine look-ahead.
  * Universe is today's DEFAULT_UNIVERSE -> survivorship bias (today's list is
    not the list you would have screened from back then).
  * No memory_context. The live committee also receives accumulated memory; the
    eval DB has none, so this measures a slightly different agent.
  * News is empty unless a Finnhub key is configured. The live agent has the same
    gap today, so this particular difference is currently nil.
  * One recent window is one market regime. Even a clean result here is weak
    evidence about behaviour in other regimes.
  * SELL IS UNTESTABLE WITHOUT --hold. With no held symbols every call passes
    held=False and committee_service._clamp_action rewrites any "sell" to "hold",
    so a `no_sells` flag on such a run is an artifact of this harness, NOT
    evidence the committee won't sell — and some "hold" rows are clamped sells.
    Pass --hold SYM1,SYM2 to evaluate those symbols as held (they are then scored
    every day even when they miss the screen top-k, mirroring the live cycle).
  * Fail-safe rows (LLM unavailable/invalid -> hold with confidence 0.0) land in
    the same table as real verdicts and will drag the hold rate up and the mean
    confidence down. Count `confidence <= 0.05` before comparing two runs.

Bars and news ARE as-of correct (both are date-windowed by briefing_service), and
the macro regime is computed as of each replayed date.

NOT part of the offline pytest suite (lives under scripts/, outside pytest's
testpaths=["tests"]). Requires network + a Gemini key: ONE LLM call per replayed
symbol-date, so --dates 10 --top-k 5 costs 50 calls.

Usage:
    uv run python -m scripts.replay_eval --dry-run          # plan only, no LLM calls
    uv run python -m scripts.replay_eval --dates 10 --top-k 5
    uv run python -m scripts.replay_eval --dates 10 --hold AAPL,JPM   # test sell too
    uv run python -m scripts.replay_eval --report-only      # re-score existing eval DB
"""

import argparse
import datetime as dt
import json
import sys

from app.config import get_settings
from app.data.cache import CachedPriceProvider
from app.data.fundamentals_edgar import EdgarFundamentalsProvider
from app.data.news_factory import build_news_provider
from app.data.prices_yfinance import YFinancePriceProvider
from app.llm.gemini import GeminiClient
from app.screener.universe import DEFAULT_UNIVERSE
from app.services.analysis_service import run_screen_on_bars
from app.services.briefing_service import get_stock_briefing
from app.services.committee_service import run_committee
from app.services.market_data_service import fetch_bars
from app.services.market_regime_service import get_regime, regime_context_line
from app.services.scorecard_service import build_forward_returns, build_scorecard
from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.decision_repo import get_decisions, save_decision

REPLAY_MODE = "replay"  # never a live trading mode; only ever written by this script
DEFAULT_DB = "scripts/replay_eval.db"


def _weekdays_back(end: dt.date, count: int) -> list:
    """count 个工作日(倒着数,再正序返回)。假日不剔除——落在假日的 as_of
    只会让 entry 回退到前一根 bar,不影响正确性。"""
    out = []
    day = end
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day -= dt.timedelta(days=1)
    return sorted(out)


def _replay_one_date(session, as_of, providers, gemini, top_k, lookback_days,
                     held_symbols=frozenset(), verbose=True):
    """一个交易日:screen -> 逐标的 briefing+committee -> 存 mode=replay 决策。
    返回本日新增决策数。已有该日决策则跳过(可断点续跑,不重复烧 LLM)。

    held_symbols 里的标的按"已持有"评估(held=True),并且**即使没进 screen 前
    top_k 也照样评**——与线上 cycle 的 _eval_symbols 语义一致(持仓必须每轮都
    有机会被卖掉)。没有持仓时 sell 在 _clamp_action 里会被改写成 hold,那样
    整个卖出行为根本无法测量(见文件头 KNOWN BIASES)。
    """
    price_provider, news_provider, funds_provider = providers

    if get_decisions(session, as_of):
        if verbose:
            print(f"  {as_of}  已有决策,跳过(--report-only 可直接看报告)")
        return 0

    start = as_of - dt.timedelta(days=lookback_days)
    bars, _skipped = fetch_bars(price_provider, DEFAULT_UNIVERSE, start, as_of)
    scores = run_screen_on_bars(bars, top_k)
    candidates = [s.symbol for s in scores]
    # 持仓补在候选后面(去重),保证每个持仓每天都被评估一次
    eval_symbols = candidates + [s for s in sorted(held_symbols) if s not in candidates]
    if not eval_symbols:
        print(f"  {as_of}  screen 无候选(行情不足),跳过")
        return 0

    # 宏观背景按当日算(as-of 正确),整日复用一次,与线上 cycle 一致。
    try:
        market_context = regime_context_line(get_regime(price_provider, as_of))
    except Exception as exc:  # noqa: BLE001 - 研究脚本:降级但不中断
        print(f"  {as_of}  regime 计算失败({exc}),本日不带宏观背景")
        market_context = ""

    written = 0
    for symbol in eval_symbols:
        held = symbol in held_symbols
        try:
            briefing = get_stock_briefing(symbol, price_provider, news_provider,
                                          funds_provider, as_of)
            # memory_context 留空(见文件头偏差说明)。
            committee = run_committee(gemini, briefing, held=held,
                                      market_context=market_context)
            save_decision(session, as_of, symbol, committee["action"],
                          committee["confidence"], REPLAY_MODE,
                          json.dumps(committee, ensure_ascii=False), held=held)
            written += 1
            if verbose:
                print(f"  {as_of}  {symbol:6s} {'HELD' if held else '    '} "
                      f"{committee['action']:5s} conf={committee['confidence']:.2f}")
        except Exception as exc:  # noqa: BLE001 - 单只失败不该中断整轮回放
            print(f"  {as_of}  {symbol:6s} FAILED: {exc}")
    session.commit()
    return written


def _print_report(session, price_provider, horizons):
    shape = build_scorecard(session)
    print("\n" + "=" * 72)
    print("决策形状(分布)")
    print("=" * 72)
    print(f"  样本 {shape['total']} 条 / {shape['distinct_symbols']} 只标的 "
          f"/ {shape['as_of_from']} 至 {shape['as_of_to']}")
    print(f"  动作 buy {shape['by_action_pct']['buy']:.1%} "
          f"hold {shape['by_action_pct']['hold']:.1%} "
          f"sell {shape['by_action_pct']['sell']:.1%}")
    conf = shape["confidence"]
    print(f"  置信度 mean={conf['mean']} median={conf['median']} stdev={conf['stdev']} "
          f"min={conf['min']} max={conf['max']}")
    print(f"  flags: {[f['code'] for f in shape['flags']]}")

    fwd = build_forward_returns(session, price_provider, horizons=tuple(horizons))
    print("\n" + "=" * 72)
    print("决策是否奏效(前瞻收益)")
    print("=" * 72)
    print(f"  {fwd['note']}")
    for h in fwd["horizons"]:
        block = fwd["by_horizon"][str(h)]
        cov = block["coverage"]
        print(f"\n  --- {h} 个交易日后 --- "
              f"成熟 {cov['matured']} / 未到期 {cov['pending']} / 无行情 {cov['unpriced']}")
        if cov["matured"] == 0:
            print("      尚无成熟数据,无法评价")
            continue
        for action in ("buy", "sell", "hold"):
            s = block["by_action"][action]
            if s["n"]:
                hit = "—" if s["hit_rate"] is None else f"{s['hit_rate']:.1%}"
                # n 后面永远跟 标的数/天数:同标的相邻日期高度自相关,n 会严重
                # 高估独立观测数(14条sell其实只有3只标的)
                print(f"      {action:5s} n={s['n']:3d} "
                      f"({s['distinct_symbols']}只标的/{s['distinct_days']}天) "
                      f"均值={s['mean_return_pct']:+.3f}% "
                      f"中位={s['median_return_pct']:+.3f}% 命中={hit} ({s['hit_rate_meaning']})")
        print("      买入按置信度分桶:")
        for b in block["buy_by_confidence"]:
            if b["n"]:
                hit = "—" if b["hit_rate"] is None else f"{b['hit_rate']:.1%}"
                print(f"        {b['bucket']:>9s}  n={b['n']:3d} "
                      f"均值={b['mean_return_pct']:+.3f}% 命中={hit}")
        sig = block["confidence_signal"]
        gate = f"      信号门控: n={sig['n']} distinct_days={sig['distinct_days']}"
        if sig.get("t_stat") is not None:
            gate += (f" t={sig['t_stat']} (临界 {sig['t_critical']}) "
                     f"显著={sig['significant']}")
        if sig.get("dominant_confidence_share") is not None:
            gate += f" 最常见置信度占比={sig['dominant_confidence_share']:.0%}"
        print(gate)
        print(f"      => {sig['verdict'] or sig.get('note')}")
        if sig.get("caveat"):
            print(f"      ⚠ {sig['caveat']}")

    print("\n" + "=" * 72)
    print("偏差提醒(见文件头):基本面非 point-in-time(用的是今天的财报)、")
    print("universe 有幸存者偏差、无 memory_context、单一市场区间。")
    print("即使结论看起来干净,也只是弱证据。")
    print("=" * 72)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dates", type=int, default=10,
                        help="回放多少个交易日(默认 10)")
    parser.add_argument("--top-k", type=int, default=5,
                        help="每个交易日取 screen 前几名(默认 5)。总 LLM 调用 = dates × top-k")
    parser.add_argument("--end-days-ago", type=int, default=8,
                        help="最近一个回放日距今多少天(默认 8,让 5 日 horizon 有机会成熟)")
    parser.add_argument("--horizons", default="1,5",
                        help="前瞻收益 horizon,逗号分隔(默认 1,5)")
    parser.add_argument("--hold", default="",
                        help="逗号分隔的标的,按已持有评估(held=True)。不给的话 sell "
                             "会被 _clamp_action 改写成 hold,卖出行为完全测不到")
    parser.add_argument("--db", default=DEFAULT_DB,
                        help=f"独立评测库路径(默认 {DEFAULT_DB});绝不写线上库")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印计划与成本,不建库、不调用 LLM")
    parser.add_argument("--report-only", action="store_true",
                        help="不再回放,直接对已有评测库出报告")
    args = parser.parse_args(argv)

    horizons = [int(x) for x in args.horizons.split(",") if x.strip().isdigit()] or [1, 5]
    end = dt.date.today() - dt.timedelta(days=args.end_days_ago)
    dates = _weekdays_back(end, args.dates)
    held_symbols = frozenset(s.strip().upper() for s in args.hold.split(",") if s.strip())

    if args.dry_run:
        per_day = args.top_k + len(held_symbols)  # 持仓即使没进 top_k 也评
        print(f"计划回放 {len(dates)} 个交易日: {dates[0]} .. {dates[-1]}")
        print(f"每日 top-{args.top_k}"
              + (f" + 持仓 {sorted(held_symbols)}" if held_symbols else "")
              + f" → 最多 {len(dates) * per_day} 次 Gemini 调用")
        print(f"写入独立库: {args.db}(线上 stockagent.db 不受影响)")
        print(f"前瞻 horizon: {horizons}")
        if not held_symbols:
            print("⚠ 未指定 --hold:sell 会被改写成 hold,卖出行为测不到")
        return 0

    settings = get_settings()
    if not settings.gemini_api_key and not args.report_only:
        print("没有配置 STOCKAGENT_GEMINI_API_KEY,无法回放委员会。", file=sys.stderr)
        return 2

    price_provider = CachedPriceProvider(YFinancePriceProvider(), settings.cache_dir)
    providers = (price_provider, build_news_provider(settings),
                 EdgarFundamentalsProvider(settings.edgar_user_agent))
    gemini = GeminiClient() if settings.gemini_api_key else None

    engine = make_engine(args.db)
    init_db(engine)
    with make_session_factory(engine)() as session:
        if not args.report_only:
            print(f"回放 {len(dates)} 个交易日到独立库 {args.db} "
                  f"(线上库不受影响),每日 top-{args.top_k}"
                  + (f",持仓 {sorted(held_symbols)} 按 held=True 评估" if held_symbols
                     else ",无持仓(sell 测不到)") + ":")
            total = 0
            for as_of in dates:
                total += _replay_one_date(session, as_of, providers, gemini,
                                          args.top_k, settings.lookback_days,
                                          held_symbols=held_symbols)
            print(f"\n新增 {total} 条 mode={REPLAY_MODE} 决策")
        _print_report(session, price_provider, horizons)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
