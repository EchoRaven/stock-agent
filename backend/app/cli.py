import argparse
import datetime as dt
import sys
from pathlib import Path

from app.backtest.engine import BacktestConfig, BacktestEngine
from app.cli_trading import register as register_trading
from app.config import get_settings
from app.data.cache import CachedPriceProvider
from app.data.fundamentals_edgar import EdgarFundamentalsProvider
from app.data.news_factory import build_news_provider
from app.data.prices_yfinance import YFinancePriceProvider
from app.factors.miner import mine_factors
from app.llm.gemini import GeminiClient
from app.report.markdown import render_backtest_report, render_screen_report
from app.screener.universe import load_universe
from app.services.analysis_service import default_screener, run_screen_on_bars
from app.services.market_data_service import fetch_bars
from app.services.news_sentiment_service import get_symbol_sentiment
from app.services.reflection_service import reflect_on_closed_trades
from app.services.report_service import generate_daily_report
from app.services.trade_cycle_service import run_trade_cycle
from app.store.db import init_db, make_engine, make_session_factory
from app.util.trading_day import et_trading_day


def _positive_top_n(value: str) -> int:
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("--top must be >= 1")
    return n


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stock-agent", description="量化底座 + M2 日报 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    screen = sub.add_parser("screen", help="运行筛选器并输出报告")
    screen.add_argument("--universe", type=Path, default=None, help="股票池文件,缺省用内置池")
    screen.add_argument("--top", type=_positive_top_n, default=None, help="输出前 N 名(必须 >= 1)")
    screen.add_argument("--reports-dir", type=Path, default=None)

    bt = sub.add_parser("backtest", help="quant-only 回测")
    bt.add_argument("--start", type=dt.date.fromisoformat, required=True)
    bt.add_argument("--end", type=dt.date.fromisoformat, required=True)
    bt.add_argument("--cash", type=float, default=100_000.0)
    bt.add_argument("--max-positions", type=int, default=5)
    bt.add_argument("--universe", type=Path, default=None)
    bt.add_argument("--reports-dir", type=Path, default=None)

    rep = sub.add_parser("report", help="生成当日(或指定日)盘后日报")
    rep.add_argument("--date", type=dt.date.fromisoformat, default=None)
    rep.add_argument("--reports-dir", type=Path, default=None)

    sent = sub.add_parser("sentiment", help="抓取近期新闻并用 LLM 打情绪分(前瞻能力,非历史回测)")
    sent.add_argument("symbol", help="股票代码,如 AAPL")
    sent.add_argument("--days", type=int, default=7, help="回看新闻天数(默认 7)")
    sent.add_argument("--max-items", type=int, default=10, help="最多打分的新闻条数(默认 10)")
    sent.add_argument("--date", type=dt.date.fromisoformat, default=None, help="as_of 日期,缺省=今日(ET)")

    tc = sub.add_parser("trade-cycle",
                        help="screen → 四角色委员会(Gemini)→ 闸门下单 的每日交易循环")
    tc.add_argument("--max-eval", type=int, default=None, help="最多评估的标的数(缺省=全部)")
    tc.add_argument("--no-settle", action="store_true", help="跳过本轮撮合(只提交订单)")

    sub.add_parser("reflect", help="对已平仓模拟盘交易补写复盘(均价法已实现盈亏 "
                                   "+ 可选 LLM 教训);正常情况下 trade-cycle 每轮"
                                   "已自动跑过,这是手动补跑通道")

    mf = sub.add_parser("mine-factors",
                        help="evidence-gated 自主因子挖掘:LLM 只提出受限目录内的结构化"
                             "因子提案(不产出/执行任何代码)→ 双窗口回测门禁 → 写入知识库")
    mf.add_argument("--n", type=int, default=3, help="本轮提案数量上限,1-5(默认 3)")

    register_trading(sub)
    return parser


def _default_provider(settings):
    return CachedPriceProvider(YFinancePriceProvider(), settings.cache_dir)


def _write_report(reports_dir: Path, filename: str, text: str) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / filename
    path.write_text(text)
    return path


def _warn_skipped(skipped: list) -> None:
    if not skipped:
        return
    detail = ", ".join(f"{sym}({reason})" for sym, reason in skipped)
    print(f"[warn] 跳过 {len(skipped)} 个标的: {detail}")


def cmd_screen(args, provider=None) -> int:
    settings = get_settings()
    provider = provider or _default_provider(settings)
    as_of = et_trading_day(dt.datetime.now(dt.UTC))
    symbols = load_universe(args.universe)
    top_n = args.top if args.top is not None else settings.top_n
    start = as_of - dt.timedelta(days=settings.lookback_days)
    bars, skipped = fetch_bars(provider, symbols, start, as_of)
    _warn_skipped(skipped)
    scores = run_screen_on_bars(bars, top_n)
    text = render_screen_report(scores, as_of)
    path = _write_report(args.reports_dir or settings.reports_dir,
                         f"screen_{as_of.strftime('%Y%m%d')}.md", text)
    print(text)
    print(f"[report saved] {path}")
    return 0


def cmd_backtest(args, provider=None) -> int:
    settings = get_settings()
    provider = provider or _default_provider(settings)
    symbols = load_universe(args.universe)
    config = BacktestConfig(start=args.start, end=args.end,
                            initial_cash=args.cash, max_positions=args.max_positions)
    fetch_start = args.start - dt.timedelta(days=config.lookback_days)
    bars, skipped = fetch_bars(provider, symbols, fetch_start, args.end)
    _warn_skipped(skipped)
    result = BacktestEngine(bars, default_screener(), config).run()
    text = render_backtest_report(result, config)
    reports_dir = args.reports_dir or settings.reports_dir
    name = f"backtest_{args.start.isoformat()}_{args.end.isoformat()}"
    path = _write_report(reports_dir, f"{name}.md", text)
    result.equity_curve.to_csv(reports_dir / f"{name}.csv", header=["equity"])
    print(text)
    print(f"[report saved] {path}")
    return 0


def cmd_report(args, session=None) -> int:
    """盘后日报(当日 signals + decisions 汇总):落库 + 写文件。薄壳,业务在 report_service。"""
    settings = get_settings()
    report_date = args.date or et_trading_day(dt.datetime.now(dt.UTC))
    own_session = session is None
    if own_session:
        engine = make_engine(settings.db_path)
        init_db(engine)
        session = make_session_factory(engine)()
    try:
        text, path = generate_daily_report(session, report_date,
                                           args.reports_dir or settings.reports_dir)
    finally:
        if own_session:
            session.close()
    print(text)
    print(f"[report saved] {path}")
    return 0


def cmd_sentiment(args, news_provider=None, gemini_client=None) -> int:
    """新闻情绪打分 CLI 薄壳:抓近期新闻 + (可选)LLM 打分。业务在 news_sentiment_service。"""
    settings = get_settings()
    news_provider = news_provider or build_news_provider(settings)
    as_of = args.date or et_trading_day(dt.datetime.now(dt.UTC))
    has_key = bool(settings.gemini_api_key)
    if gemini_client is None and has_key:
        gemini_client = GeminiClient()
    if not has_key and gemini_client is None:
        print("[warn] Gemini 未配置(STOCKAGENT_GEMINI_API_KEY 缺失):仅列出新闻,不打分。")
    result = get_symbol_sentiment(
        news_provider, gemini_client, args.symbol, as_of,
        days=args.days, max_items=args.max_items,
        score=(gemini_client is not None),
    )
    print(f"# 情绪 {result['symbol']} @ {result['as_of']}(近 {result['days']} 天,共 {result['news_count']} 条新闻)")
    if result["sentiment"] is None:
        print("情绪分: 未打分" + ("(无近期新闻)" if result["news_count"] == 0 else "(Gemini 未配置)"))
    else:
        print(f"情绪分: {result['sentiment']:+.3f}  (-1 极负 / 0 中性 / +1 极正)")
    for h in result["headlines"]:
        print(f"  - [{h['date']}] ({h['source']}) {h['headline']}")
    print("\n注:情绪分基于当前新闻的前瞻性信号,非历史回测验证的 alpha。")
    return 0


def cmd_trade_cycle(args, session=None, provider=None, news_provider=None,
                    fundamentals_provider=None, gemini_client=None) -> int:
    """screen → 委员会 → 闸门下单 的每日交易循环薄壳。业务全在 trade_cycle_service;
    这里只装配 provider/session 与打印可读摘要。委员会只出建议——是否真的成交仍由
    trade_cycle_service 内部经 submit_decision → RiskGate 决定,CLI 不做任何判断。"""
    settings = get_settings()
    provider = provider or _default_provider(settings)
    news_provider = news_provider or build_news_provider(settings)
    fundamentals_provider = (fundamentals_provider
                             or EdgarFundamentalsProvider(settings.edgar_user_agent))
    if gemini_client is None and settings.gemini_api_key:
        gemini_client = GeminiClient()
    elif gemini_client is None:
        print("[warn] Gemini 未配置(STOCKAGENT_GEMINI_API_KEY 缺失):委员会将 fail-safe 为保守观望。")
    own_session = session is None
    if own_session:
        engine = make_engine(settings.db_path)
        init_db(engine)
        session = make_session_factory(engine)()
    try:
        result = run_trade_cycle(
            session, provider, news_provider, fundamentals_provider, gemini_client,
            settle=not args.no_settle, max_eval=args.max_eval,
        )
    finally:
        if own_session:
            session.close()
    print(f"# 交易循环 {result['as_of']}  mode={result['mode']}  "
          f"评估 {result['evaluated']} 只标的")
    for d in result["decisions"]:
        shares = d["shares"] if d["shares"] is not None else "-"
        note = d["submit_result"].get("note", "")
        print(f"  {d['symbol']:<6} {d['action']:<4} conf={d['confidence']:.2f} "
             f"shares={shares}  {note}")
    if result["fills"]:
        print(f"成交 {len(result['fills'])} 笔:")
        for f in result["fills"]:
            print(f"  {f['fill_date']} {f['side']} {f['symbol']} "
                 f"x{f['shares']} @ {f['price']}")
    if result["errors"]:
        print(f"[warn] {len(result['errors'])} 个标的处理失败:")
        for e in result["errors"]:
            print(f"  {e['symbol']}: {e['error']}")
    _warn_skipped(result["skipped"])
    return 0


def cmd_reflect(args, session=None, gemini_client=None) -> int:
    """平仓复盘薄壳:业务全在 reflection_service。ADVISORY CONTEXT ONLY——只写
    memory_entries,不碰下单/风控路径。gemini_client 缺省时(无 key)只写事实性
    复盘,不生成 LLM 教训(reflect_on_closed_trades 对 gemini_client=None 安全)。
    """
    settings = get_settings()
    if gemini_client is None and settings.gemini_api_key:
        gemini_client = GeminiClient()
    own_session = session is None
    if own_session:
        engine = make_engine(settings.db_path)
        init_db(engine)
        session = make_session_factory(engine)()
    try:
        reviews = reflect_on_closed_trades(session, gemini_client)
        session.commit()
    finally:
        if own_session:
            session.close()
    print(f"# 平仓复盘:新增 {len(reviews)} 条")
    for r in reviews:
        print(f"  {r['title']}")
    return 0


def cmd_mine_factors(args, session=None, provider=None, gemini_client=None) -> int:
    """自主因子挖掘薄壳:业务全在 app.factors.miner.mine_factors。LLM 只产出
    受限目录内的结构化提案(不产出/执行任何代码);每条提案的两窗口回测结果
    (validated/no_improvement/refuted)写入知识库,ADVISORY CONTEXT ONLY——
    不碰任何下单/风控路径。gemini_client 缺省且未配置 key 时用确定性种子提案
    (mine_factors 对 gemini_client=None 安全)。"""
    settings = get_settings()
    provider = provider or _default_provider(settings)
    if gemini_client is None and settings.gemini_api_key:
        gemini_client = GeminiClient()
    elif gemini_client is None:
        print("[warn] Gemini 未配置(STOCKAGENT_GEMINI_API_KEY 缺失):将使用确定性种子提案。")
    own_session = session is None
    if own_session:
        engine = make_engine(settings.db_path)
        init_db(engine)
        session = make_session_factory(engine)()
    try:
        results = mine_factors(session, provider, gemini_client, n=args.n)
        session.commit()
    finally:
        if own_session:
            session.close()
    print(f"# 因子挖掘:{len(results)} 个提案")
    for r in results:
        if r.get("verdict") == "error":
            print(f"  {r['factor']}{r['params']}  [error] {r.get('error')}")
            continue
        print(f"  {r['factor']}{r['params']}  verdict={r['verdict']}")
        for name, ws in r.get("windows", {}).items():
            print(f"    [{name}] base sharpe={ws['base']['sharpe']:.2f} "
                 f"cand sharpe={ws['cand']['sharpe']:.2f}")
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "screen":
        return cmd_screen(args)
    if args.command == "backtest":
        return cmd_backtest(args)
    if args.command == "report":
        return cmd_report(args)
    if args.command == "sentiment":
        return cmd_sentiment(args)
    if args.command == "trade-cycle":
        return cmd_trade_cycle(args)
    if args.command == "reflect":
        return cmd_reflect(args)
    if args.command == "mine-factors":
        return cmd_mine_factors(args)
    return args.func(args)  # M3 子命令(orders/mode/watchdog)经 set_defaults 分发


if __name__ == "__main__":
    sys.exit(main())
