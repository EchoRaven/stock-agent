"""M3 交易 CLI 薄壳:orders(list/approve/reject/settle)、mode(show/set)、watchdog。

业务全部在 execution/order_manager 与 watchdog/monitor;这里只做参数解析与装配。
"""
import datetime as dt
import json

from app.config import get_settings
from app.data.cache import CachedPriceProvider
from app.data.prices_yfinance import YFinancePriceProvider
from app.execution.order_manager import (approve_order, list_pending, reject_order,
                                         settle_open)
from app.services.market_data_service import latest_closes_for, open_prices_for
from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.order_repo import (STATUS_SUBMITTED, get_order,
                                        get_orders_by_status)
from app.store.repos.paper_repo import get_positions
from app.store.repos.settings_repo import MODES, get_mode, set_mode
from app.util.trading_day import et_trading_day
from app.watchdog.monitor import check_and_enforce


def open_cli_session():
    settings = get_settings()
    engine = make_engine(settings.db_path)
    init_db(engine)
    return make_session_factory(engine)()


def _default_provider():
    return CachedPriceProvider(YFinancePriceProvider(), get_settings().cache_dir)


def register(sub) -> None:
    orders = sub.add_parser("orders", help="订单队列:list/approve/reject/settle")
    orders.add_argument("action", choices=["list", "approve", "reject", "settle"])
    orders.add_argument("order_id", nargs="?", type=int, default=None)
    orders.add_argument("--date", type=dt.date.fromisoformat, default=None,
                        help="settle 撮合日/approve 评估日(缺省=今天 ET)")
    orders.set_defaults(func=cmd_orders)

    mode = sub.add_parser("mode", help="查看/切换运行模式(唯一真相在 DB settings)")
    mode.add_argument("action", choices=["show", "set"])
    mode.add_argument("value", nargs="?", choices=list(MODES), default=None)
    mode.add_argument("--confirm-full-auto", action="store_true",
                      help="开启 full_auto 必须显式加此参数(安全红线)")
    mode.set_defaults(func=cmd_mode)

    wd = sub.add_parser("watchdog", help="cron 心跳检查;异常自动降级 advisory")
    wd.set_defaults(func=cmd_watchdog)


def cmd_orders(args, provider=None) -> int:
    provider = provider or _default_provider()
    as_of = args.date or et_trading_day(dt.datetime.now(dt.UTC))
    with open_cli_session() as session:
        if args.action == "list":
            rows = list_pending(session)
            if not rows:
                print("(no pending orders)")
            for row in rows:
                print(f"#{row['id']} {row['as_of']} {row['side']} {row['symbol']} "
                      f"x{row['shares']} [{row['status']}]")
            return 0
        if args.action == "settle":
            symbols = sorted({o.symbol for o in
                              get_orders_by_status(session, STATUS_SUBMITTED)})
            open_prices = open_prices_for(provider, symbols, as_of) if symbols else {}
            fills = settle_open(session, as_of, open_prices)
            session.commit()
            print(f"{len(fills)} fill(s)")
            for fill in fills:
                print(f"  {fill['fill_date']} {fill['side']} {fill['symbol']} "
                      f"x{fill['shares']} @ {fill['price']}")
            return 0
        if args.order_id is None:
            print("[error] approve/reject 需要 order_id")
            return 2
        if args.action == "approve":
            order = get_order(session, args.order_id)
            symbols = sorted(({order.symbol} if order else set())
                             | set(get_positions(session)))
            prices = latest_closes_for(provider, symbols, as_of) if symbols else {}
            result = approve_order(session, args.order_id, as_of, prices)
        else:
            result = reject_order(session, args.order_id)
        session.commit()
        print(result["note"])
        if result["order"]:
            print(result["order"])
        return 0


def cmd_mode(args) -> int:
    with open_cli_session() as session:
        if args.action == "show":
            print(f"mode: {get_mode(session)}")
            session.commit()
            return 0
        if args.value is None:
            print("[error] mode set 需要一个值(advisory/semi_auto/full_auto)")
            return 2
        try:
            set_mode(session, args.value, confirm_full_auto=args.confirm_full_auto)
        except ValueError as exc:
            print(f"[error] {exc}")
            return 2
        session.commit()
        print(f"mode set to {args.value}")
        return 0


def cmd_watchdog(args) -> int:
    with open_cli_session() as session:
        result = check_and_enforce(session, dt.datetime.now(dt.UTC).replace(tzinfo=None))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["healthy"] else 1
