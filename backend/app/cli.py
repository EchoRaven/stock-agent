import argparse
import datetime as dt
import sys
from pathlib import Path

from app.backtest.engine import BacktestConfig, BacktestEngine
from app.config import get_settings
from app.data.cache import CachedPriceProvider
from app.data.prices_yfinance import YFinancePriceProvider
from app.report.markdown import render_backtest_report, render_screen_report
from app.screener.universe import load_universe
from app.services.analysis_service import default_screener, run_screen


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stock-agent", description="M1 量化底座 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    screen = sub.add_parser("screen", help="运行筛选器并输出报告")
    screen.add_argument("--universe", type=Path, default=None, help="股票池文件,缺省用内置池")
    screen.add_argument("--top", type=int, default=None, help="输出前 N 名")
    screen.add_argument("--reports-dir", type=Path, default=None)

    bt = sub.add_parser("backtest", help="quant-only 回测")
    bt.add_argument("--start", type=dt.date.fromisoformat, required=True)
    bt.add_argument("--end", type=dt.date.fromisoformat, required=True)
    bt.add_argument("--cash", type=float, default=100_000.0)
    bt.add_argument("--max-positions", type=int, default=5)
    bt.add_argument("--universe", type=Path, default=None)
    bt.add_argument("--reports-dir", type=Path, default=None)
    return parser


def _default_provider(settings):
    return CachedPriceProvider(YFinancePriceProvider(), settings.cache_dir)


def _write_report(reports_dir: Path, filename: str, text: str) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / filename
    path.write_text(text)
    return path


def cmd_screen(args, provider=None) -> int:
    settings = get_settings()
    provider = provider or _default_provider(settings)
    as_of = dt.date.today()
    symbols = load_universe(args.universe)
    top_n = args.top or settings.top_n
    scores = run_screen(provider, symbols, top_n, settings.lookback_days, as_of)
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
    bars = {sym: provider.get_daily_bars(sym, fetch_start, args.end) for sym in symbols}
    bars = {sym: df for sym, df in bars.items() if not df.empty}
    result = BacktestEngine(bars, default_screener(), config).run()
    text = render_backtest_report(result, config)
    reports_dir = args.reports_dir or settings.reports_dir
    name = f"backtest_{args.start.isoformat()}_{args.end.isoformat()}"
    path = _write_report(reports_dir, f"{name}.md", text)
    result.equity_curve.to_csv(reports_dir / f"{name}.csv", header=["equity"])
    print(text)
    print(f"[report saved] {path}")
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "screen":
        return cmd_screen(args)
    return cmd_backtest(args)


if __name__ == "__main__":
    sys.exit(main())
