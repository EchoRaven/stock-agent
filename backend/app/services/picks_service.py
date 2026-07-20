"""AI 荐股(委员会排序的推荐列表)—— 在量化筛选(quant screen)之上,对筛出的
每个候选标的再跑一次 LLM 四角色委员会,按 conviction(action 优先级 +
confidence + quant_score)重新排序,产出更有说服力的候选清单。

安全红线:纯分析——本模块绝不导入 app.services.decision_service.submit_decision
或 app.execution.order_manager 的任何下单路径,不落库、不生成任何
DecisionRow/OrderRow/持仓变化(见 tests/services/test_picks_service.py 的
no-order/no-decision 断言)。调用方(app/api/routes_picks.py)会对每个候选
触发一次(可能付费的)Gemini 调用,因此挂 require_token,同
/api/stock/{symbol}/analyze /api/trade/cycle 同款门禁模式——本模块本身不关心
门禁,只负责编排。

单只候选的材料抓取/委员会任一环节异常都被本地捕获记入 errors,不让一只标的的
故障中断整轮生成(其余候选照常评估),同 trade_cycle_service 的既有约定。
"""
import datetime as dt
import logging

from app.config import get_settings
from app.screener.universe import DEFAULT_UNIVERSE
from app.services.analysis_service import run_screen_on_bars
from app.services.briefing_service import get_stock_briefing
from app.services.committee_service import run_committee
from app.services.market_data_service import fetch_bars
from app.services.memory_service import get_committee_context
from app.store.repos.paper_repo import get_positions
from app.util.trading_day import et_trading_day

logger = logging.getLogger(__name__)

MAX_CHAIR_VERDICT_LEN = 300

# 排序主键:action 优先级(buy 最靠前,sell 最靠后);次键 confidence 降序;
# 第三键 quant_score 降序(见下方 sort key)。
_ACTION_PRIORITY = {"buy": 0, "hold": 1, "sell": 2}


def generate_picks(
    session,
    price_provider,
    news_provider,
    fundamentals_provider,
    gemini_client,
    now_utc: dt.datetime | None = None,
    *,
    n: int = 8,
) -> dict:
    """跑一轮"量化筛选 + 委员会精评"荐股。分析only:绝不下单。

    返回 {as_of, n, picks, errors, skipped, gemini_calls}:
    - picks:每个 {symbol, quant_score, action, confidence, chair_verdict,
      held, rank},已按 conviction 排好序(rank 从 1 开始)。
    - errors:[{symbol, error}, ...],单只候选的材料/委员会故障记录在此,不影响
      其余候选。
    - skipped:筛选阶段抓取行情失败/无数据的标的([(symbol, reason), ...],
      见 market_data_service.fetch_bars)。
    - gemini_calls:实际触发的 Gemini 调用次数(gemini_client 为 None 时恒为
      0——fail-safe hold 并不是真的调用了 LLM)。
    """
    now = now_utc or dt.datetime.now(dt.UTC)
    as_of = et_trading_day(now)
    settings = get_settings()

    start = as_of - dt.timedelta(days=settings.lookback_days)
    bars, skipped = fetch_bars(price_provider, DEFAULT_UNIVERSE, start, as_of)
    scores = run_screen_on_bars(bars, n)

    positions = get_positions(session)

    picks: list[dict] = []
    errors: list[dict] = []
    gemini_calls = 0

    for score in scores:
        sym = score.symbol
        try:
            briefing = get_stock_briefing(sym, price_provider, news_provider,
                                          fundamentals_provider, as_of)
            memory_context = get_committee_context(session, sym)
            held = sym in positions
            committee = run_committee(gemini_client, briefing, held=held,
                                      memory_context=memory_context)
        except Exception as exc:
            logger.warning("picks: candidate %s failed, skipping", sym, exc_info=True)
            errors.append({"symbol": sym, "error": str(exc) or type(exc).__name__})
            continue

        if gemini_client is not None:
            gemini_calls += 1

        picks.append({
            "symbol": sym,
            "quant_score": round(score.total, 4),
            "action": committee["action"],
            "confidence": committee["confidence"],
            "chair_verdict": committee["chair"]["verdict"][:MAX_CHAIR_VERDICT_LEN],
            "held": held,
        })

    picks.sort(key=lambda p: (
        _ACTION_PRIORITY.get(p["action"], 1), -p["confidence"], -p["quant_score"],
    ))
    for rank, pick in enumerate(picks, start=1):
        pick["rank"] = rank

    return {
        "as_of": as_of.isoformat(),
        "n": n,
        "picks": picks,
        "errors": errors,
        "skipped": skipped,
        "gemini_calls": gemini_calls,
    }
