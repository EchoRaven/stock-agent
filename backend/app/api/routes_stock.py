"""GET /api/stock/{symbol} —— 股票详情页(价格序列 + 统计 + 新闻 + 财报),只读、
无 token 门禁(不触发付费 LLM 调用,与 /api/sentiment /api/trade/cycle 那类不同)。

POST /api/stock/{symbol}/analyze —— 按需触发一次 Gemini 四角色委员会分析。

安全红线:analyze 是纯分析端点,绝不下单——本文件不导入
app.services.decision_service / app.execution.order_manager 的任何下单路径,
只读 get_positions 判断 held、调用 run_committee 拿裁决草稿并原样返回展示,
不落库、不生成订单(见 tests/api/test_stock.py 的 no-order/no-decision 断言)。
它会触发一次(可能付费的)Gemini 调用,因此挂 require_token,同 /api/sentiment
/api/trade/cycle 同款门禁模式。

外部数据源(价格/新闻/财报)全部走 app.api.deps 的专用依赖注入,测试整体覆盖
注入 fake,离线。GET 端点里新闻/财报各自 try/except——单个源失败只让对应字段
退化为空,不拖垮整页;价格失败(无数据)才是 404。
"""
import datetime as dt
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import (get_fundamentals_provider, get_gemini_client, get_news_provider,
                          get_provider, get_session)
from app.api.security import require_token
from app.config import get_settings
from app.data.base import PriceProvider
from app.data.fundamentals_edgar import FundamentalsProvider
from app.data.news_finnhub import NewsProvider
from app.data.sanitize import sanitize_text
from app.llm.gemini import GeminiClient
from app.services.briefing_service import get_stock_briefing, summarize_bars
from app.services.committee_service import run_committee
from app.services.market_data_service import fetch_bars
from app.services.memory_service import get_committee_context
from app.store.repos.paper_repo import get_positions
from app.util.trading_day import et_trading_day

logger = logging.getLogger(__name__)

router = APIRouter(tags=["stock"])

DEFAULT_DAYS = 365
MIN_DAYS = 30
MAX_DAYS = 1825
NEWS_LOOKBACK_DAYS = 14
MAX_NEWS_ITEMS = 15
MAX_FUNDAMENTAL_POINTS = 8


def _num(value):
    """float 化;NaN/不可转换 → None;保留 4 位小数(同 briefing_service._num 的约定)。"""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else round(f, 4)


def _price_series(bars) -> list:
    return [
        {"date": ts.date().isoformat(), "close": _num(row["close"]), "volume": _num(row["volume"])}
        for ts, row in bars.iterrows()
    ]


def _extended_summary(bars) -> dict:
    summary = summarize_bars(bars)
    close = bars["close"]
    n = len(close)
    chg_1d = _num(close.iloc[-1] / close.iloc[-2] - 1) if n > 1 and close.iloc[-2] else None
    summary["chg_1d"] = chg_1d
    summary["pct_1d"] = _num(chg_1d * 100) if chg_1d is not None else None
    summary["pct_5d"] = _num(summary["chg_5d"] * 100) if summary.get("chg_5d") is not None else None
    summary["pct_20d"] = _num(summary["chg_20d"] * 100) if summary.get("chg_20d") is not None else None
    summary["high_52w"] = _num(close.max()) if n > 0 else None
    summary["low_52w"] = _num(close.min()) if n > 0 else None
    return summary


def _fetch_news(news_provider: NewsProvider, symbol: str, as_of: dt.date) -> list:
    try:
        items = news_provider.get_company_news(
            symbol, as_of - dt.timedelta(days=NEWS_LOOKBACK_DAYS), as_of)
    except Exception:
        logger.warning("stock detail: news fetch failed for %s", symbol, exc_info=True)
        return []
    return [
        {
            "date": item.published_at.isoformat(),
            "source": sanitize_text(item.source, 60),
            "headline": sanitize_text(item.headline, 200),
            "summary": sanitize_text(item.summary, 500),
            "url": item.url,
        }
        for item in items[:MAX_NEWS_ITEMS]
    ]


def _fund_points(points) -> list:
    return [{"end": p.end.isoformat(), "value": p.value, "fiscal": p.fiscal}
            for p in list(points)[:MAX_FUNDAMENTAL_POINTS]]


def _fetch_fundamentals(fundamentals_provider: FundamentalsProvider, symbol: str) -> dict:
    try:
        funds = fundamentals_provider.get_fundamentals(symbol)
    except Exception:
        logger.warning("stock detail: fundamentals fetch failed for %s", symbol, exc_info=True)
        return {"revenue": [], "net_income": [], "eps": []}
    return {
        "revenue": _fund_points(funds.revenue),
        "net_income": _fund_points(funds.net_income),
        "eps": _fund_points(funds.eps),
    }


def _clean_symbol(symbol: str) -> str:
    sym = (symbol or "").strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol must not be empty")
    return sym


@router.get("/stock/{symbol}")
def get_stock_route(
    symbol: str,
    days: int = Query(DEFAULT_DAYS, ge=MIN_DAYS, le=MAX_DAYS),
    provider: PriceProvider = Depends(get_provider),
    news_provider: NewsProvider = Depends(get_news_provider),
    fundamentals_provider: FundamentalsProvider = Depends(get_fundamentals_provider),
) -> dict:
    sym = _clean_symbol(symbol)
    try:
        as_of = et_trading_day(dt.datetime.now(dt.UTC))
        start = as_of - dt.timedelta(days=days)
        bars_map, _skipped = fetch_bars(provider, [sym], start, as_of)
        bars = bars_map.get(sym)
        if bars is None or bars.empty:
            raise HTTPException(status_code=404, detail=f"no price data for {sym}")

        return {
            "symbol": sym,
            "as_of": as_of.isoformat(),
            "days": days,
            "price_series": _price_series(bars),
            "summary": _extended_summary(bars),
            "news": _fetch_news(news_provider, sym, as_of),
            "fundamentals": _fetch_fundamentals(fundamentals_provider, sym),
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("stock detail failed for %s", sym)
        raise HTTPException(status_code=500,
                            detail=f"stock detail failed: {type(exc).__name__}") from exc


@router.post("/stock/{symbol}/analyze", dependencies=[Depends(require_token)])
def analyze_stock_route(
    symbol: str,
    provider: PriceProvider = Depends(get_provider),
    news_provider: NewsProvider = Depends(get_news_provider),
    fundamentals_provider: FundamentalsProvider = Depends(get_fundamentals_provider),
    gemini_client: GeminiClient | None = Depends(get_gemini_client),
    session: Session = Depends(get_session),
) -> dict:
    sym = _clean_symbol(symbol)
    if gemini_client is None and not get_settings().gemini_api_key:
        raise HTTPException(status_code=400,
                            detail="Gemini 未配置(STOCKAGENT_GEMINI_API_KEY),无法 AI 分析")
    try:
        as_of = et_trading_day(dt.datetime.now(dt.UTC))
        briefing = get_stock_briefing(sym, provider, news_provider, fundamentals_provider, as_of)
        held = sym in get_positions(session)
        memory_context = get_committee_context(session, sym)
        # 分析only:run_committee 只产出裁决草稿,这里直接原样返回展示——不落库、
        # 不调用 decision_service.submit_decision、不经 order_manager,不生成任何
        # 订单/持仓变化(见 tests/api/test_stock.py 的 no-order/no-decision 断言)。
        result = run_committee(gemini_client, briefing, held=held, memory_context=memory_context)
        return {
            "symbol": sym,
            "as_of": as_of.isoformat(),
            "held": held,
            "committee": result["committee"],
            "chair": result["chair"],
            "action": result["action"],
            "confidence": result["confidence"],
            "note": "analysis only — no order placed",
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("stock analyze failed for %s", sym)
        raise HTTPException(status_code=500,
                            detail=f"stock analyze failed: {type(exc).__name__}") from exc
