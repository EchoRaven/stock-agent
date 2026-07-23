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

成本优化(非风控决策):逐标的循环前先做一次纯读取的容量预筛
(_capacity_impossible_filter),提前排除"买入已经确定性不可能通过闸门"的
非持仓候选(当日新开仓配额已耗尽 / 仍在冷却期),省下材料抓取 + 委员会
(Gemini)调用——mirror(不导入!)app/risk/rules.py 的 MaxNewPositionsRule/
CooldownRule 判定口径。这只能"少评估几个反正会被拒的标的",绝不能让任何
订单被放行/创建/改变股数——真正"是否放行"的唯一权威依旧是下面的
submit_decision → RiskGate;预筛读取失败或判断有误,最坏后果也只是多评估
几个标的,不会导致任何本不该发生的下单。持仓标的永远不因此被跳过(committee
仍可能判定卖出,与买入容量无关)。
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
from app.services.market_regime_service import get_regime, regime_context_line
from app.services.memory_service import get_committee_context
from app.services.reflection_service import reflect_on_closed_trades
from app.services.analysis_service import run_screen_on_bars
from app.store.repos.order_repo import STATUS_SUBMITTED, buy_symbols_today, get_orders_by_status
from app.store.repos.paper_repo import get_account, get_positions, last_sell_dates
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


def _capacity_impossible_filter(session, eval_symbols: list, positions: dict,
                                as_of: dt.date, app_settings) -> tuple:
    """容量预筛(纯成本优化,非风控决策)——提前排除"买入已经确定性不可能通过
    闸门"的非持仓候选,省下材料抓取 + 委员会(Gemini)调用。

    Mirror(不导入!)app/risk/rules.py 里 MaxNewPositionsRule/CooldownRule 的判定
    口径,只读同样的仓储数据(buy_symbols_today/last_sell_dates)。真正"是否
    放行"的唯一权威仍是下面的 submit_decision → RiskGate;这里判断错了/读取
    失败,最坏后果也只是"多评估了几个反正会被拒的标的",绝不会导致任何本
    不该发生的下单——因此任何异常都保守降级为"不跳过任何标的"(评估全部,
    即今天的行为)。持仓标的(committee 仍可能判定卖出,与买入容量无关)永远
    不跳过。

    返回 (kept, skipped);skipped 每项 {"symbol": ..., "reason": ...}。
    """
    try:
        bought_today = buy_symbols_today(session, as_of)
        remaining_new = app_settings.max_new_positions_per_day - len(bought_today)
        last_sells = last_sell_dates(session)
    except Exception:
        logger.exception(
            "trade cycle: capacity pre-filter failed to read state, "
            "evaluating all candidates (fail-safe, skip nothing)")
        return list(eval_symbols), []

    kept, skipped = [], []
    for sym in eval_symbols:
        if sym in positions:
            kept.append(sym)  # held: 可能被判卖出,与买入容量无关,永不跳过
            continue
        last_sell = last_sells.get(sym)
        if last_sell is not None and (as_of - last_sell).days < app_settings.cooldown_days:
            skipped.append({"symbol": sym, "reason": (
                f"cooldown: sold on {last_sell}, "
                f"{app_settings.cooldown_days}-day cooldown active")})
        elif sym not in bought_today and remaining_new <= 0:
            skipped.append({"symbol": sym, "reason": (
                f"max new positions per day "
                f"({app_settings.max_new_positions_per_day}) reached")})
        else:
            kept.append(sym)
    return kept, skipped


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

    eval_symbols, skipped_no_capacity = _capacity_impossible_filter(
        session, eval_symbols, positions, as_of, app_settings)
    if skipped_no_capacity:
        cooldown_n = sum(1 for s in skipped_no_capacity if s["reason"].startswith("cooldown"))
        logger.info(
            "trade cycle: capacity pre-filter skipped %d candidate(s) before the LLM "
            "(%d cooldown, %d max-new-positions); evaluating %d",
            len(skipped_no_capacity), cooldown_n,
            len(skipped_no_capacity) - cooldown_n, len(eval_symbols))
    else:
        logger.info("trade cycle: capacity pre-filter skipped 0 candidates; evaluating %d",
                   len(eval_symbols))

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

    # ADVISORY CONTEXT ONLY(同 memory_context 款红线):大盘 regime(SPY vs 200
    # 日均线)只算这一次,给本轮所有标的复用——不是每只标的各抓一次 SPY(SPY 是
    # 大盘背景,与具体标的无关)。取价失败/规则计算异常都不能中断整轮循环,兜底
    # 降级为空字符串(不喂进委员会 prompt,不是 crash)。
    try:
        regime = get_regime(price_provider, as_of)
        market_context = regime_context_line(regime)
    except Exception:
        logger.exception("trade cycle: market regime computation failed")
        market_context = ""

    decisions = []
    errors = []
    fills = []
    gemini_calls = 0
    for symbol in eval_symbols:
        try:
            held = symbol in positions
            briefing = get_stock_briefing(symbol, price_provider, news_provider,
                                          fundamentals_provider, as_of)
            # ADVISORY CONTEXT ONLY:我们自己积累的知识 + 该票历史决策,只喂进委员会
            # prompt 作参考,不改变闸门/下单路径(见 app/services/memory_service.py)。
            memory_context = get_committee_context(session, symbol)
            committee = run_committee(gemini_client, briefing, held=held,
                                      memory_context=memory_context,
                                      market_context=market_context)
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
                # restrict_to={symbol}:环内撮合只碰当前标的,绝不因单标的价格字典
                # 把别的标的遗留的挂单跨标的误 cancel(money-path review Finding B)。
                # 当前标的取不到价时,不在环内强行处理(留给轮末扫尾用更全的价源),
                # 避免把一张本可成交的单当即以"无开盘价"取消掉。
                if price is not None:
                    fills.extend(settle_open(session, as_of, {symbol: price},
                                             restrict_to={symbol}))
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

    # ADVISORY CONTEXT ONLY(Phase 2,与 memory_context 同款红线):对本轮及以往
    # 已平仓的模拟盘持仓写一次性复盘(均价法确定性算出的已实现盈亏 + 可选 LLM
    # 教训),幂等(以 sell_fill_id 为键),只写 memory_entries,不改变本轮任何
    # 下单/撮合结果。任何失败都不能中断已经跑完的交易循环本身。
    try:
        trade_reviews = reflect_on_closed_trades(session, gemini_client, now=now)
        session.commit()
    except Exception:
        logger.exception("trade cycle: post-mortem reflection failed")
        trade_reviews = []

    return {
        "as_of": as_of.isoformat(),
        "mode": get_mode(session),
        "evaluated": len(eval_symbols),
        "skipped": skipped,
        "skipped_no_capacity": skipped_no_capacity,
        "errors": errors,
        "decisions": decisions,
        "fills": fills,
        "gemini_calls": gemini_calls,
        "trade_reviews": trade_reviews,
    }
