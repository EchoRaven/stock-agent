"""Historical full-cycle simulation — power on the learning loop (M8).

WHY THIS EXISTS (the load-bearing M8 problem): the learning loop
(reflection → trade_review → committee memory → factor mining) is BUILT but
"not powered on". The live paper account almost never closes positions, so
`reflect_on_closed_trades` almost never fires, so the committee has never
actually learned from its own round-trips. `scripts/replay_eval.py` doesn't
help here — it only records committee DECISIONS, it never fills/holds/sells, so
it produces zero closed trades and zero trade_reviews.

This script drives the FULL `run_trade_cycle` (screen → committee → gated order
→ settle/fill → reflect_on_closed_trades) over a sequence of historical trading
days in an ISOLATED DB. Positions open and close over the window, closed trades
get post-mortems written to memory, and the committee reads those reviews on
later days — i.e. the loop runs with real fuel, fast, without waiting weeks of
wall-clock for the live account to slowly turn over.

ISOLATION (critical): writes to a SEPARATE SQLite file (default
scripts/replay_loop.db), NEVER the live stockagent.db. It DOES exercise the full
order/gate/settle path (that's the point), but only inside the isolated DB with
its own mode/account. The live paper account is untouched.

KNOWN BIASES — same as replay_eval.py, read before trusting any number:
  * Fundamentals are NOT point-in-time (EdgarFundamentalsProvider ignores as_of)
    → look-ahead; worse the further back the window.
  * Universe is today's list → survivorship bias.
  * One window is one (or few) market regimes → weak evidence about others.
  * News empty unless a Finnhub key is set (same gap as the live agent today).
Bars ARE as-of correct (date-windowed by briefing_service); the macro regime is
computed as of each simulated day.

COST: full cycle = one Gemini call per evaluated symbol per day (candidates +
held positions). A 20-day window with --top-n 3 over a small universe is on the
order of ~100 calls. Scale the window/universe to your budget.

NOT part of the offline pytest suite (lives under scripts/). Requires network +
a Gemini key.

Usage:
    uv run python -m scripts.replay_loop --dry-run
    uv run python -m scripts.replay_loop --start 2025-03-10 --end 2025-04-11 --top-n 3
    uv run python -m scripts.replay_loop --report-only        # re-summarize existing DB
"""

import argparse
import datetime as dt
import sys

from app.config import get_settings
from app.data.cache import CachedPriceProvider
from app.data.fundamentals_edgar import EdgarFundamentalsProvider
from app.data.news_factory import build_news_provider
from app.data.prices_yfinance import YFinancePriceProvider
from app.llm.gemini import GeminiClient
from app.services.reflection_service import reconstruct_closed_trades
from app.services.trade_cycle_service import run_trade_cycle
from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.memory_repo import get_entries
from app.store.repos.paper_repo import get_account, get_positions
from app.store.repos.settings_repo import MODE_FULL_AUTO, get_app_settings, set_mode

DEFAULT_DB = "scripts/replay_loop.db"
# 小而波动的池子:窗口有限时更容易产生开→平的完整往返(闭环的燃料)
DEFAULT_UNIVERSE = ["AAPL", "MSFT", "NVDA", "META", "AMD", "JPM", "V", "KO"]


def _weekdays(start: dt.date, end: dt.date) -> list:
    days, d = [], start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += dt.timedelta(days=1)
    return days


def _summarize(session, universe) -> None:
    closed = reconstruct_closed_trades(session)
    reviews = get_entries(session, kind="trade_review")
    positions = get_positions(session)
    account = get_account(session, get_app_settings(session).initial_cash)

    print("\n" + "=" * 72)
    print("学习闭环燃料(已平仓交易 + 复盘)")
    print("=" * 72)
    print(f"  已平仓往返: {len(closed)} 笔")
    if closed:
        wins = [t for t in closed if t["realized_pnl"] > 0]
        total = sum(t["realized_pnl"] for t in closed)
        avg_hold = sum(t["holding_days"] for t in closed) / len(closed)
        print(f"  胜率: {len(wins)}/{len(closed)} = {len(wins)/len(closed):.0%}"
              f"  |  已实现盈亏合计: {total:+.2f}"
              f"  |  平均持有 {avg_hold:.1f} 天")
        print("  逐笔(最多 12):")
        for t in closed[:12]:
            print(f"    {t['symbol']:6s} 卖出 {t['sell_date']} 持有 "
                  f"{t['holding_days']:3d}天  盈亏 {t['realized_pnl']:+.2f} "
                  f"({t['realized_pnl_pct']:+.1f}%)")
    print(f"\n  写入 trade_review 记忆条目: {len(reviews)} 条"
          f"  ← 这就是喂回委员会的燃料(get_committee_context 会读它)")
    print(f"  期末: 现金 {account.cash:.0f} / 持仓 {len(positions)} 只")

    print("\n" + "=" * 72)
    print("偏差提醒:基本面非 point-in-time、universe 幸存者偏差、单一/少数区间。")
    print("这是把闭环'通电'并量产复盘样本的机制验证,不是策略有效性证明。")
    print("=" * 72)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--start", default="2025-03-10", help="窗口起(交易日,含)")
    parser.add_argument("--end", default="2025-04-11", help="窗口止(交易日,含)")
    parser.add_argument("--top-n", type=int, default=3, help="每日 screen 取前几名候选")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"独立库(默认 {DEFAULT_DB});绝不写线上库")
    parser.add_argument("--universe", default=",".join(DEFAULT_UNIVERSE),
                        help="逗号分隔的股票池(小池子更易产生往返)")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划,不建库不调 LLM")
    parser.add_argument("--report-only", action="store_true", help="不再模拟,直接汇总已有库")
    args = parser.parse_args(argv)

    universe = [s.strip().upper() for s in args.universe.split(",") if s.strip()]
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    days = _weekdays(start, end)

    if args.dry_run:
        print(f"计划模拟 {len(days)} 个交易日: {start} .. {end}")
        print(f"股票池 {len(universe)} 只: {universe}")
        print(f"每日 top-{args.top_n} 候选 + 已持仓,各一次 Gemini 调用 → 粗估 "
              f"{len(days)} × (~{args.top_n}+持仓) 次调用")
        print(f"写入独立库: {args.db}(线上 stockagent.db 不受影响)")
        return 0

    settings = get_settings()
    if not settings.gemini_api_key and not args.report_only:
        print("没有配置 STOCKAGENT_GEMINI_API_KEY,无法跑委员会。", file=sys.stderr)
        return 2

    price_provider = CachedPriceProvider(YFinancePriceProvider(), settings.cache_dir)
    news_provider = build_news_provider(settings)
    funds_provider = EdgarFundamentalsProvider(settings.edgar_user_agent)
    gemini = GeminiClient() if settings.gemini_api_key else None

    engine = make_engine(args.db)
    init_db(engine)
    with make_session_factory(engine)() as session:
        if not args.report_only:
            # 隔离库里开 full_auto(唯一真相在 DB;绝不影响线上库的 mode)
            set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
            session.commit()
            print(f"在独立库 {args.db} 上按历史推进 full_auto 交易循环 "
                  f"({len(days)} 个交易日,池 {len(universe)} 只):")
            calls = fills = reviews = 0
            for as_of in days:
                now_utc = dt.datetime(as_of.year, as_of.month, as_of.day, 16, 0, tzinfo=dt.UTC)
                try:
                    r = run_trade_cycle(session, price_provider, news_provider,
                                        funds_provider, gemini, now_utc=now_utc,
                                        settle=True, universe=universe, max_eval=None)
                except Exception as exc:  # noqa: BLE001 - 单日故障不该中断整段模拟
                    print(f"  {as_of}  循环失败: {exc}")
                    continue
                calls += r.get("gemini_calls", 0)
                nf, nr = len(r.get("fills", [])), len(r.get("trade_reviews", []))
                fills += nf
                reviews += nr
                mark = f"  成交 {nf}" if nf else ""
                mark += f"  复盘+{nr}" if nr else ""
                print(f"  {as_of}  决策 {len(r.get('decisions', []))}{mark}")
            print(f"\n模拟完成:{calls} 次 Gemini 调用,累计成交 {fills} 笔,"
                  f"新写复盘 {reviews} 条")
        _summarize(session, universe)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
