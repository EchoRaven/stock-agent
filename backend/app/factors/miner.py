"""Phase 4:evidence-gated 自主因子挖掘——两窗口回测门禁 -> 知识库。

安全红线:ADVISORY CONTEXT ONLY,与 app/services/memory_service.py /
app/services/reflection_service.py 同款——本模块只读行情、只写
app/store/repos/memory_repo.py 的 factor 知识库条目,绝不导入、也绝不能被
app/execution/order_manager.py 或 app/risk/ 下任何下单/风控路径依赖(见
tests/test_memory_advisory_isolation.py 的自动化守卫)。唯一被真正执行的
代码是 app.factors.catalog.build_factor 装配出的手写 Rule——LLM 提案本身
永远只是数据(见 app.factors.proposer)。

诚实的门禁:必须在 MINING_WINDOWS 的**每一个**窗口上都稳健改善(夏普 +0.05
以上、最大回撤不明显恶化)才算 validated;现实预期(参见仓库里此前 4 轮策略
实验)是大多数提案会被判 no_improvement/refuted——这种"诚实"正是这套机制
存在的意义:不让 agent 相信没有证据支撑的因子。
"""
import datetime as dt
import logging

from app.backtest.engine import BacktestConfig, BacktestEngine
from app.factors.catalog import build_factor
from app.factors.proposer import propose_factors
from app.screener.base import Screener
from app.screener.rules_momentum import MomentumRule
from app.screener.rules_trend import TrendRule
from app.screener.rules_volume import VolumeRule
from app.screener.universe import DEFAULT_UNIVERSE
from app.services.analysis_service import default_screener
from app.services.market_data_service import fetch_bars
from app.store.repos.memory_repo import add_entry, get_entries

logger = logging.getLogger(__name__)

# 两个互相独立、性质不同的窗口(牛市 / 震荡),两窗都要稳健改善才算数——
# 单窗改善大概率是过拟合(参见仓库里此前几轮策略实验的教训)。
MINING_WINDOWS = [
    ("bull_2023H2", dt.date(2023, 7, 1), dt.date(2024, 6, 30)),
    ("chop_2024H2", dt.date(2024, 7, 1), dt.date(2025, 6, 30)),
]

SHARPE_MARGIN = 0.05
DRAWDOWN_MARGIN = 0.02
FETCH_LOOKBACK_DAYS = 260


def _backtest(screener, bars_by_symbol: dict, start: dt.date, end: dt.date) -> dict:
    """固定的回测配置(与 miner 之外的默认值一致:10万现金/最多5仓/门槛0.5/
    滑点5bp),只有 screener 变化——保证基线与候选公平可比。"""
    config = BacktestConfig(start=start, end=end, initial_cash=100_000.0,
                            max_positions=5, min_score=0.5, slippage_bps=5.0)
    return BacktestEngine(bars_by_symbol, screener, config).run().metrics


def _candidate_screener(factor_rule) -> Screener:
    """基线三因子各让出一部分权重给候选因子(0.2),而不是简单叠加——避免
    "因子越多分数越虚高"这种伪改善。"""
    return Screener([
        (TrendRule(), 0.3),
        (MomentumRule(), 0.3),
        (VolumeRule(), 0.2),
        (factor_rule, 0.2),
    ])


def _robust_in_window(base: dict, cand: dict) -> bool:
    return (cand["sharpe"] >= base["sharpe"] + SHARPE_MARGIN
            and cand["max_drawdown"] >= base["max_drawdown"] - DRAWDOWN_MARGIN)


def _verdict(base_metrics_by_window: dict, cand_metrics_by_window: dict) -> str:
    """validated 当且仅当**每一个**窗口都稳健改善;否则只要至少一个窗口的夏普
    跑赢基线(哪怕不稳健)就是 no_improvement(有一点信号但不够);一个窗口都
    没跑赢就是 refuted(彻底没有证据支撑)。"""
    windows = list(base_metrics_by_window.keys())
    if all(_robust_in_window(base_metrics_by_window[w], cand_metrics_by_window[w])
           for w in windows):
        return "validated"
    if any(cand_metrics_by_window[w]["sharpe"] > base_metrics_by_window[w]["sharpe"]
           for w in windows):
        return "no_improvement"
    return "refuted"


def _avoid_names(session) -> list:
    """best-effort:从已有 source="agent" 的 factor 条目 evidence_json 里读回
    试过的 factor 名(不解析 title 文本,直接读结构化证据更稳)。任何解析失败
    都跳过该条,不让历史脏数据阻断新一轮挖掘。"""
    import json

    names = []
    for row in get_entries(session, kind="factor"):
        if row.source != "agent":
            continue
        try:
            evidence = json.loads(row.evidence_json)
        except (TypeError, ValueError):
            continue
        proposal = evidence.get("proposal") if isinstance(evidence, dict) else None
        factor = proposal.get("factor") if isinstance(proposal, dict) else None
        if isinstance(factor, str):
            names.append(factor)
    return names


def _format_body(hypothesis: str, windows_summary: dict, verdict: str) -> str:
    lines = [hypothesis] if hypothesis else []
    for name, ws in windows_summary.items():
        base, cand = ws["base"], ws["cand"]
        lines.append(
            f"[{name}] 基线 sharpe={base['sharpe']:.2f} return={base['total_return']:.2%} "
            f"maxdd={base['max_drawdown']:.2%} -> 候选 sharpe={cand['sharpe']:.2f} "
            f"return={cand['total_return']:.2%} maxdd={cand['max_drawdown']:.2%}"
        )
    lines.append(f"结论:{verdict}")
    return "\n".join(lines)


def mine_factors(session, price_provider, gemini_client, *, n: int = 3) -> list:
    """LLM 只产出结构化提案(factor 名 + 整数 params),每条经目录校验、双窗口
    回测,只有稳健改善的写 validated,其余诚实地写 no_improvement/refuted——
    全部作为 kind="factor" 的知识库条目落库(ADVISORY CONTEXT ONLY)。
    """
    avoid = _avoid_names(session)
    proposals = propose_factors(gemini_client, n=n, avoid=avoid)

    bars_by_window: dict = {}
    base_metrics_by_window: dict = {}
    for name, start, end in MINING_WINDOWS:
        fetch_start = start - dt.timedelta(days=FETCH_LOOKBACK_DAYS)
        bars, _skipped = fetch_bars(price_provider, DEFAULT_UNIVERSE, fetch_start, end)
        bars_by_window[name] = bars
        base_metrics_by_window[name] = _backtest(default_screener(), bars, start, end)

    results = []
    for proposal in proposals:
        factor = proposal["factor"]
        params = proposal["params"]
        hypothesis = proposal.get("hypothesis", "")
        try:
            rule = build_factor(factor, params)
            cand_metrics_by_window = {}
            for name, start, end in MINING_WINDOWS:
                screener = _candidate_screener(rule)
                cand_metrics_by_window[name] = _backtest(
                    screener, bars_by_window[name], start, end)

            verdict = _verdict(base_metrics_by_window, cand_metrics_by_window)
            windows_summary = {
                name: {"base": base_metrics_by_window[name], "cand": cand_metrics_by_window[name]}
                for name in base_metrics_by_window
            }
            body = _format_body(hypothesis, windows_summary, verdict)
            entry = add_entry(
                session, "factor", f"因子挖掘 {factor}{params}", body,
                symbol=None, status=verdict,
                evidence={"proposal": proposal, "windows": windows_summary, "verdict": verdict},
                source="agent",
            )
            results.append({
                "factor": factor, "params": params, "verdict": verdict,
                "windows": windows_summary, "entry_id": entry.id,
            })
        except Exception as exc:
            logger.warning("factor mining: proposal %s failed", proposal, exc_info=True)
            results.append({"factor": factor, "params": params, "verdict": "error",
                            "error": str(exc)})
    return results
