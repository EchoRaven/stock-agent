"""组合真实新闻(news provider)+ Gemini 打分 → 单只标的情绪结果。
安全:打分走 score_news_sentiment(内部 sanitize_text+wrap_untrusted 注入定界、
clamp[-1,1]、fail-safe 0.0);展示字段单独 sanitize_text 清洗。"""
import datetime as dt
import logging

from app.data.sanitize import sanitize_text
from app.services.sentiment_service import score_news_sentiment

logger = logging.getLogger(__name__)


def get_symbol_sentiment(news_provider, gemini_client, symbol: str, as_of: dt.date,
                         *, days: int = 7, max_items: int = 10, score: bool = True) -> dict:
    """取 [as_of-days, as_of] 的近期新闻并(可选)LLM 打分。
    返回 JSON 可序列化 dict。无新闻 / score=False → sentiment=None。绝不抛异常。"""
    sym = symbol.strip().upper()
    start = as_of - dt.timedelta(days=days)
    items = news_provider.get_company_news(sym, start, as_of)  # 已 []-safe
    items = items[:max_items]

    headlines_display = [
        {"date": n.published_at.isoformat(),
         "source": sanitize_text(n.source, 60),
         "headline": sanitize_text(n.headline, 200)}
        for n in items
    ]
    result = {
        "symbol": sym,
        "as_of": as_of.isoformat(),
        "days": days,
        "news_count": len(items),
        "sentiment": None,   # None = 未打分(无新闻 / 未启用 / 无 key);float = 已打分
        "scored": False,
        "headlines": headlines_display,
    }
    if not items or not score:
        return result

    # 传原始 headline+summary 文本给打分器(注入清洗在 score_news_sentiment 内部完成)
    texts = [f"{n.headline}. {n.summary}".strip() for n in items]
    result["sentiment"] = score_news_sentiment(gemini_client, texts, sym)  # float, 已 clamp+fail-safe
    result["scored"] = True
    return result
