import argparse
import datetime as dt
import sys
from pathlib import Path

from app.backtest.engine import BacktestConfig, BacktestEngine
from app.cli_trading import register as register_trading
from app.config import get_settings
from app.data.cache import CachedPriceProvider
from app.data.news_factory import build_news_provider
from app.data.prices_yfinance import YFinancePriceProvider
from app.llm.gemini import GeminiClient
from app.report.markdown import render_backtest_report, render_screen_report
from app.screener.universe import load_universe
from app.services.analysis_service import default_screener, run_screen_on_bars
from app.services.market_data_service import fetch_bars
from app.services.news_sentiment_service import get_symbol_sentiment
from app.services.report_service import generate_daily_report
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
    return args.func(args)  # M3 子命令(orders/mode/watchdog)经 set_defaults 分发


if __name__ == "__main__":
    sys.exit(main())
