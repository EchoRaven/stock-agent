"""screen → committee → gated 下单 → (可选)撮合 的每日交易循环编排。

这是 full_auto 资金路径的核心编排层("量化筛选 + LLM 定夺")。安全红线:
- 委员会(committee_service.run_committee)只出建议——真正决定是否成交的唯一
  权威是 decision_service.submit_decision → order_manager 的下单 choke point,
  它读 DB 里的 mode(唯一真相)并对非 hold 决定强制过 RiskGate;本模块自己
  不做任何"是否放行"的判断,只负责把委员会的建议(经 clamp)与服务端算出的
  股数拼成合法 payload 交给 submit_decision;
- shares 全部由服务端根据账户权益 + 单票仓位上限算出,绝不采信 LLM 给的任何
  数字(委员会草案里根本不含 shares 字段);
- 单只标的的材料抓取/委员会/提交任一环节异常都被本地捕获记入 errors,不让
  一只标的的故障中断整轮循环(其余标的照常评估);
- settle=True 时逐标的立即撮合(而非整轮循环结束后一次性撮合),让持仓/现金
  在下一只标的过闸门前就已落库,TotalPositionCapRule/SinglePositionCapRule
  才能在同一轮里跨标的累计生效,而不是每笔都读循环开始前的同一份快照
  (defense-in-depth finding:聚合仓位上限此前不跨标的联动)。
"""
import datetime as dt
import logging

from app.config import get_settings
from app.execution.order_manager import settle_open
from app.screener.universe import DEFAULT_UNIVERSE
from app.services.briefing_service import get_stock_briefing
from app.services.committee_service import run_committee
from app.services.decision_service import TRADE_ACTIONS, submit_decision
from app.services.market_data_service import fetch_bars, latest_closes_for, open_prices_for
from app.services.analysis_service import run_screen_on_bars
from app.store.repos.order_repo import STATUS_SUBMITTED, get_orders_by_status
from app.store.repos.paper_repo import get_account, get_positions
from app.store.repos.settings_repo import get_app_settings, get_mode
from app.util.trading_day import et_trading_day

logger = logging.getLogger(__name__)


def _eval_symbols(candidates: list, positions: dict, max_eval) -> list:
    """候选(筛选出的潜在买入)在前,已持仓但未入选的标的补在后面;按需截断。"""
    ordered = []
    seen = set()
    for sym in list(candidates) + [s for s in positions if s not in candidates]:
        if sym not in seen:
            seen.add(sym)
            ordered.append(sym)
    return ordered[:max_eval] if max_eval is not None else ordered


def _size_shares(action: str, symbol: str, held: bool, price, equity: float,
                 single_position_cap_pct: float, positions: dict):
    """服务端计算股数——LLM 的委员会草案里没有 shares 字段,这里是唯一算股数的地方。

    返回 (final_action, shares_or_None)。买入按目标单票仓位(权益 ×
    single_position_cap_pct)除以价格取整股;算出 <= 0 或拿不到有效价格,
    一律回退为 hold(不是拒绝——只是这轮不产生交易意图,真正的仓位上限判定
    仍归 RiskGate)。卖出按当前持仓全部股数(全平)。
    """
    if action == "buy":
        if held or price is None or price <= 0:
            return "hold", None
        budget = equity * single_position_cap_pct
        shares = int(budget // price)
        if shares <= 0:
            return "hold", None
        return "buy", shares
    if action == "sell":
        if not held:
            return "hold", None
        return "sell", positions[symbol].shares
    return "hold", None


def run_trade_cycle(session, price_provider, news_provider, fundamentals_provider,
                    gemini_client, now_utc: dt.datetime | None = None, *,
                    settle: bool = True, universe: list | None = None,
                    max_eval: int | None = None) -> dict:
    now = now_utc or dt.datetime.now(dt.UTC)
    as_of = et_trading_day(now)
    app_settings = get_app_settings(session)
    cfg = get_settings()
    uni = universe or DEFAULT_UNIVERSE

    start = as_of - dt.timedelta(days=cfg.lookback_days)
    bars, skipped = fetch_bars(price_provider, uni, start, as_of)
    scores = run_screen_on_bars(bars, cfg.top_n)
    candidates = [s.symbol for s in scores]

    positions = get_positions(session)
    eval_symbols = _eval_symbols(candidates, positions, max_eval)

    prices = latest_closes_for(price_provider, sorted(set(eval_symbols) | set(positions)), as_of)

    account = get_account(session, app_settings.initial_cash)
    equity = account.cash + sum(
        pos.shares * prices.get(sym, pos.avg_cost) for sym, pos in positions.items()
    )

    # 撮合价:优先用撮合日开盘价(next-open 语义);但盘前/非交易时段当日开盘价
    # 尚未发布时,回退到最近收盘价(latest close,即上面 prices,一段过去价格,
    # 非未来函数),让模拟盘能按最优可得价立即成交,而不是因"无开盘价"被整轮撤单。
    # 安全红线(见模块顶部+settle_open 文档):settle_open 会撤销所有当前
    # STATUS_SUBMITTED 但 symbol 不在传入价格字典里的订单——下面逐标的撮合时只
    # 传 {symbol: price} 单标的字典,绝不能把这份全量 fill_prices 整个传进去。
    fill_prices = ({**prices, **open_prices_for(price_provider, eval_symbols, as_of)}
                   if settle else {})

    decisions = []
    errors = []
    fills = []
    gemini_calls = 0
    for symbol in eval_symbols:
        try:
            held = symbol in positions
            briefing = get_stock_briefing(symbol, price_provider, news_provider,
                                          fundamentals_provider, as_of)
            committee = run_committee(gemini_client, briefing, held=held)
            if gemini_client is not None:
                gemini_calls += 1
            action, shares = _size_shares(
                committee["action"], symbol, held, prices.get(symbol), equity,
                app_settings.single_position_cap_pct, positions)
            payload = {
                "symbol": symbol,
                "as_of": as_of.isoformat(),
                "action": action,
                "confidence": committee["confidence"],
                "committee": committee["committee"],
                "chair": committee["chair"],
            }
            if action in TRADE_ACTIONS:
                payload["shares"] = shares
            result = submit_decision(session, payload, prices=prices, now_utc=now)
            decisions.append({
                "symbol": symbol, "action": action,
                "confidence": committee["confidence"], "shares": shares,
                "submit_result": result,
            })
            # 安全红线核心:full_auto 买/卖过闸门后立刻撮合(而不是拖到整轮循环
            # 结束后一次性撮合),让持仓/现金马上落库,下一只标的的闸门判定才能
            # 看见累计后的敞口——否则 TotalPositionCapRule/SinglePositionCapRule
            # 在同一轮里全部读的是循环开始前的同一份快照,永远不会跨标的联动。
            # semi_auto 走 PENDING_CONFIRMATION(等人工批准),不是 SUBMITTED,
            # 这里不会误撮合;advisory/hold 根本没有 "order" 键。
            order = result.get("order")
            if settle and order is not None and order["status"] == STATUS_SUBMITTED:
                price = fill_prices.get(symbol)
                symbol_open_prices = {symbol: price} if price is not None else {}
                fills.extend(settle_open(session, as_of, symbol_open_prices))
                session.commit()
        except Exception as exc:
            logger.exception("trade cycle failed for %s", symbol)
            errors.append({"symbol": symbol, "error": str(exc)})

    if settle:
        # 兜底扫尾:正常情况下本轮产生的每笔 SUBMITTED 订单都已在上面逐标的撮合
        # 掉了(FILLED/CANCELLED,不会再是 SUBMITTED),这里只会捞到本轮之外遗留
        # 的(如上次 settle=False 跑过的)订单——不会对本轮订单重复撮合。
        leftover_symbols = sorted({
            row.symbol for row in get_orders_by_status(session, STATUS_SUBMITTED)
        })
        if leftover_symbols:
            leftover_prices = {
                **latest_closes_for(price_provider, leftover_symbols, as_of),
                **open_prices_for(price_provider, leftover_symbols, as_of),
            }
            fills.extend(settle_open(session, as_of, leftover_prices))
            session.commit()

    return {
        "as_of": as_of.isoformat(),
        "mode": get_mode(session),
        "evaluated": len(eval_symbols),
        "skipped": skipped,
        "errors": errors,
        "decisions": decisions,
        "fills": fills,
        "gemini_calls": gemini_calls,
    }
