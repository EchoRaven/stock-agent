"""Agent 知识库服务(Phase 1):委员会检索的"我们自己积累的知识"。

安全红线:ADVISORY CONTEXT ONLY——本模块产出的文本只作为
app.services.committee_service 提示词里一段说明性上下文,绝不被
app/execution/order_manager.py 或 app/risk/ 下任何下单/风控路径导入,不可能
改变 RiskGate 的判定(见 tests/test_memory_advisory_isolation.py)。

这里的内容是我们自己写的内部知识(可信,不是外部材料),格式上仍与
app.data.sanitize.wrap_untrusted(仅用于外部新闻的定界包裹)完全区分开——
不要把这里的文本当成"不可信外部内容"处理。
"""
import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.store.models import DecisionRow, MemoryEntryRow
from app.store.repos.memory_repo import add_entry, get_entries

logger = logging.getLogger(__name__)

SEED_SOURCE = "seed_experiment"
_GENERAL_KINDS = ("insight", "factor")
_MAX_BODY_LEN = 300
_MAX_VERDICT_LEN = 120

KNOWLEDGE_HEADER = "【已积累的知识/教训(内部,仅供参考)】"
DECISIONS_HEADER = "【本票历史决策】"

# ---------------------------------------------------------------------------
# 种子内容:4 轮策略实验的真实、有证据支撑的结论 + 2 条元洞察(原文,勿改写)。
# ---------------------------------------------------------------------------
SEED_ENTRIES = [
    {
        "kind": "factor",
        "status": "refuted",
        "source": SEED_SOURCE,
        "title": "技术因子叠加(波动率/趋势质量/相对强度)",
        "body": (
            "在基线(趋势.4/动量.4/量能.2)上叠加这三类因子,两窗口回测无稳健改善:"
            "弱窗看着最优的组合(combined_all/相对强度)在牛窗显著跑输=过拟合;"
            "唯一两窗都稳健的是波动率过滤(降回撤)但系统性削收益、夏普未改善。"
            "结论:保持基线默认,不采纳变体。"
        ),
        "evidence": {"windows": 2, "report": "docs/strategy_experiment_report.md"},
    },
    {
        "kind": "factor",
        "status": "refuted",
        "source": SEED_SOURCE,
        "title": "止损/仓位管理(fixed_pct/atr/trailing/portfolio_dd)",
        "body": (
            "四种止损两窗口回测也无稳健改善:fixed_pct/trailing 弱窗温和降回撤但牛窗"
            "几乎不触发;atr 两窗都差;portfolio_dd 只看牛窗像有效、弱窗灾难"
            "(whipsaw/收益转负)。结论:简单止损修不了基线弱点。"
        ),
        "evidence": {"windows": 2, "report": "docs/stoploss_experiment_report.md"},
    },
    {
        "kind": "factor",
        "status": "refuted",
        "source": SEED_SOURCE,
        "title": "市场状态择时(SPY vs 200日均线)",
        "body": (
            "regime_flat(SPY跌破200日均线→清仓持现金)在慢熊2022(亏损-27.6%→-15.3%、"
            "最大回撤-37%→-15%腰斩)和震荡2024H2(+3%→+13%)有效,但V型急跌急涨2020"
            "灾难:收益24.8%→2.8%且回撤不降反增(-26%→-34%)——200SMA太慢,卖在底部又"
            "踏空反弹(whipsaw)。强形态依赖,不作默认。"
        ),
        "evidence": {"windows": 4, "report": "docs/regime_experiment_report.md"},
    },
    {
        "kind": "factor",
        "status": "refuted",
        "source": SEED_SOURCE,
        "title": "股票池分散(扩到54只跨板块)",
        "body": (
            "手选54只跨板块看着改善,但用回测起点当时的完整point-in-time S&P500"
            "(非手选)每窗都大幅崩坏(牛市+30%→-13%)——证明上一轮'分散更好'几乎全是"
            "我手选赢家的幸存者偏差。机制:动量筛选器在500只大池里追短期暴涨的低质"
            "小票。低相关不是驱动,股票池的精选质量才是。保持默认30只精选池。"
        ),
        "evidence": {"windows": 4, "report": "docs/universe_pit_report.md"},
    },
    {
        "kind": "insight",
        "status": "active",
        "source": SEED_SOURCE,
        "title": "元结论:简单技术叠加无稳健免费改进",
        "body": (
            "因子/止损/regime择时/股票池四轮实验一致:简单技术叠加都强形态依赖、没有"
            "稳健的免费改进;基线本质是随市场的beta,其相对稳健恰恰依赖那30只精选"
            "优质大盘池。真正未被证伪、仍待验证的根本杠杆=M2 LLM委员会在基本面/事件"
            "层面的增益。"
        ),
        "evidence": {"experiments": 4},
    },
    {
        "kind": "insight",
        "status": "data_blocked",
        "source": SEED_SOURCE,
        "title": "新闻情绪=前瞻能力,非验证过的alpha",
        "body": (
            "Gemini能对当前新闻打情绪分(前瞻性使用),但'新闻情绪能否提升收益'的"
            "历史alpha回测数据受阻(yfinance无历史新闻+逐日逐票LLM成本不现实)。"
            "别把它当成已验证的alpha因子。"
        ),
        "evidence": {"blocked": "historical news data"},
    },
]


def ensure_seeded(session: Session) -> int:
    """幂等播种:仅当没有任何 source="seed_experiment" 条目时插入全部
    SEED_ENTRIES;已播种过则不重复插入,返回本次插入条数(0 或 6)。"""
    already = session.scalars(
        select(MemoryEntryRow.id).where(MemoryEntryRow.source == SEED_SOURCE).limit(1)
    ).first()
    if already is not None:
        return 0
    for entry in SEED_ENTRIES:
        add_entry(
            session, entry["kind"], entry["title"], entry["body"],
            symbol=entry.get("symbol"),
            status=entry.get("status", "active"),
            evidence=entry.get("evidence"),
            source=entry.get("source", SEED_SOURCE),
            weight=entry.get("weight", 1.0),
        )
    logger.info("memory: seeded %d entries", len(SEED_ENTRIES))
    return len(SEED_ENTRIES)


def _truncate(text: str, max_len: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def _knowledge_line(row: MemoryEntryRow) -> str:
    tag = f"{row.kind}|{row.status}" if row.status and row.status != "active" else row.kind
    body = _truncate(row.body, _MAX_BODY_LEN)
    evidence = row.evidence_json
    suffix = f" (evidence: {evidence})" if evidence and evidence != "{}" else ""
    return f"- [{tag}] {row.title}: {body}{suffix}"


def _recent_decisions(session: Session, symbol: str, limit: int) -> list[DecisionRow]:
    stmt = (
        select(DecisionRow)
        .where(DecisionRow.symbol == symbol)
        .order_by(DecisionRow.as_of.desc(), DecisionRow.id.desc())
        .limit(limit)
    )
    return list(session.scalars(stmt))


def _decision_line(row: DecisionRow) -> str:
    verdict = ""
    try:
        payload = json.loads(row.payload_json)
        chair = payload.get("chair") if isinstance(payload, dict) else None
        if isinstance(chair, dict):
            verdict = _truncate(chair.get("verdict") or "", _MAX_VERDICT_LEN)
    except (TypeError, ValueError):
        verdict = ""
    chair_part = f" → (chair: {verdict})" if verdict else ""
    return f"- {row.as_of.isoformat()} {row.action} conf{row.confidence:.2f}{chair_part}"


def get_committee_context(session: Session, symbol: str, *, max_insights: int = 6,
                          max_decisions: int = 3) -> str:
    """组装喂给委员会 prompt 的内部知识 + 该票历史决策文本块。

    ADVISORY CONTEXT ONLY——纯只读检索,不涉及任何下单/风控路径。惰性播种:
    首次调用时若知识库里还没有种子条目会先 ensure_seeded。返回空字符串代表
    无可用上下文,调用方(committee_service)据此省略整个提示词小节。
    """
    ensure_seeded(session)

    sym = (symbol or "").strip().upper()

    general: list[MemoryEntryRow] = []
    for kind in _GENERAL_KINDS:
        general.extend(row for row in get_entries(session, kind=kind) if row.symbol is None)
    symbol_specific = get_entries(session, symbol=sym) if sym else []
    seen_ids = {row.id for row in general}
    knowledge = general + [row for row in symbol_specific if row.id not in seen_ids]
    knowledge.sort(key=lambda r: (r.weight, r.updated_at), reverse=True)
    knowledge = knowledge[:max_insights]

    decisions = _recent_decisions(session, sym, max_decisions) if sym else []

    if not knowledge and not decisions:
        return ""

    lines = []
    if knowledge:
        lines.append(KNOWLEDGE_HEADER)
        lines.extend(_knowledge_line(row) for row in knowledge)
    if decisions:
        lines.append(DECISIONS_HEADER)
        lines.extend(_decision_line(row) for row in decisions)
    return "\n".join(lines)
