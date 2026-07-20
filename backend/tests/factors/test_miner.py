"""app.factors.miner(Phase 4):evidence-gated 自主因子挖掘。

安全红线:mine_factors 只写 app/store/repos/memory_repo.py 的 factor 知识库
条目(ADVISORY CONTEXT ONLY,见 tests/test_memory_advisory_isolation.py 的
独立守卫),不碰任何下单/风控路径;唯一被执行的代码来自
app.factors.catalog.build_factor(受目录约束)。这里覆盖:
(a) _verdict 的三档判定(validated / no_improvement / refuted)——门槛必须
    在全部窗口都稳健改善才算 validated;
(b) mine_factors 端到端(合成行情 + fake gemini):跑通、写库、返回结果、
    再次运行会把已试过的因子名带进 avoid;
(c) 单条提案的回测异常不打断整批挖掘。
"""
import datetime as dt
import json

import numpy as np
import pandas as pd
import pytest

from app.data.base import PriceProvider, empty_bars
from app.factors import miner
from app.factors.catalog import build_factor
from app.factors.miner import MINING_WINDOWS, _backtest, _candidate_screener, _verdict, mine_factors
from app.screener.universe import DEFAULT_UNIVERSE
from app.services.analysis_service import default_screener
from app.services.market_data_service import fetch_bars
from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.memory_repo import get_entries


# ---------------------------------------------------------------------------
# _verdict: 构造好的 metrics dict,不跑真实回测。
# ---------------------------------------------------------------------------


def _metrics(sharpe: float, max_drawdown: float) -> dict:
    return {"sharpe": sharpe, "max_drawdown": max_drawdown, "total_return": 0.0,
           "win_rate": 0.0, "num_fills": 0.0}


def test_verdict_validated_when_every_window_robustly_improves():
    base = {"w1": _metrics(0.5, -0.10), "w2": _metrics(0.3, -0.15)}
    cand = {"w1": _metrics(0.6, -0.10), "w2": _metrics(0.4, -0.16)}  # +0.10 sharpe, dd within -0.02
    assert _verdict(base, cand) == "validated"


def test_verdict_refuted_when_no_window_beats_baseline_sharpe():
    base = {"w1": _metrics(0.5, -0.10), "w2": _metrics(0.3, -0.15)}
    cand = {"w1": _metrics(0.4, -0.10), "w2": _metrics(0.2, -0.15)}
    assert _verdict(base, cand) == "refuted"


def test_verdict_no_improvement_when_only_some_windows_beat_sharpe():
    base = {"w1": _metrics(0.5, -0.10), "w2": _metrics(0.3, -0.15)}
    cand = {"w1": _metrics(0.6, -0.10), "w2": _metrics(0.2, -0.15)}  # beats w1 only
    assert _verdict(base, cand) == "no_improvement"


def test_verdict_validated_requires_sharpe_margin_not_just_any_beat():
    # w1 beats by only +0.01 (< 0.05 margin) -> that window isn't "robust".
    base = {"w1": _metrics(0.5, -0.10), "w2": _metrics(0.3, -0.15)}
    cand = {"w1": _metrics(0.51, -0.10), "w2": _metrics(0.40, -0.15)}
    assert _verdict(base, cand) == "no_improvement"  # beats sharpe both windows, but w1 not robust


def test_verdict_not_validated_when_drawdown_materially_worse_despite_sharpe_gain():
    base = {"w1": _metrics(0.5, -0.10), "w2": _metrics(0.3, -0.15)}
    # sharpe improves plenty in both windows but w1's drawdown worsens by more than the 0.02 margin
    cand = {"w1": _metrics(0.6, -0.20), "w2": _metrics(0.4, -0.16)}
    assert _verdict(base, cand) != "validated"


def test_mining_windows_default_has_two_windows_with_valid_bounds():
    # 生产默认配置(未被任何测试 monkeypatch 覆盖——本模块顶部的 import 在任何
    # fixture 跑之前就已捕获真实默认值)。
    assert len(MINING_WINDOWS) == 2
    for _name, start, end in MINING_WINDOWS:
        assert isinstance(start, dt.date) and isinstance(end, dt.date)
        assert start < end


# ---------------------------------------------------------------------------
# _backtest / _candidate_screener:纯函数式装配,直接白盒检查。
# ---------------------------------------------------------------------------


def _stable_seed(symbol: str) -> int:
    return sum(ord(c) for c in symbol)


class _SyntheticProvider(PriceProvider):
    """离线合成行情:按 symbol 派生一个确定性的、缓慢上升的走势,不发起网络请求。"""

    def get_daily_bars(self, symbol: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        if start > end:
            return empty_bars()
        idx = pd.bdate_range(start, end)
        n = len(idx)
        if n == 0:
            return empty_bars()
        seed = _stable_seed(symbol)
        base = 50.0 + (seed % 50)
        step = 0.05 + (seed % 5) * 0.02
        close = pd.Series(base + step * np.arange(n, dtype=float), index=idx)
        return pd.DataFrame(
            {"open": close - 0.3, "high": close + 0.5, "low": close - 0.5,
             "close": close, "volume": 1_000_000.0 + (seed % 7) * 10_000.0},
            index=idx,
        )


def test_backtest_returns_engine_metric_keys():
    provider = _SyntheticProvider()
    start, end = dt.date(2024, 1, 2), dt.date(2024, 1, 19)
    fetch_start = start - dt.timedelta(days=miner.FETCH_LOOKBACK_DAYS)
    bars, _skipped = fetch_bars(provider, DEFAULT_UNIVERSE, fetch_start, end)
    metrics = _backtest(default_screener(), bars, start, end)
    for key in ("total_return", "max_drawdown", "sharpe", "win_rate", "num_fills"):
        assert key in metrics


def test_candidate_screener_includes_factor_rule_at_weight_point_two():
    # 白盒检查内部工厂 _candidate_screener 的确切装配(权重 0.3/0.3/0.2/0.2)——
    # 这是它唯一的职责,直接读 Screener._rules 比重建等价回测更直接可靠。
    rule = build_factor("momentum", {"window": 60})
    screener = _candidate_screener(rule)
    weights = {r.name: w for r, w in screener._rules}
    assert weights[rule.name] == pytest.approx(0.2)
    assert weights["trend"] == pytest.approx(0.3)
    assert weights["momentum"] == pytest.approx(0.3)
    assert weights["volume"] == pytest.approx(0.2)


# ---------------------------------------------------------------------------
# mine_factors 集成:合成宇宙(注入 fake provider)+ fake gemini,短窗口保持快。
# ---------------------------------------------------------------------------


class _FakeGeminiMomentum60:
    def __init__(self):
        self.calls = 0

    def generate_json(self, prompt):
        self.calls += 1
        return {"proposals": [
            {"factor": "momentum", "params": {"window": 60}, "hypothesis": "中期动量延续"},
        ]}


_SHORT_WINDOWS = [
    ("tiny_a", dt.date(2024, 1, 2), dt.date(2024, 1, 19)),
    ("tiny_b", dt.date(2024, 2, 1), dt.date(2024, 2, 16)),
]


@pytest.fixture
def session():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


@pytest.fixture
def short_windows(monkeypatch):
    """把生产的两个整年窗口换成两个几周窗口,让"合成宇宙"测试保持 tiny/fast,
    不改变 mine_factors 本身的逻辑(只换它读取的 MINING_WINDOWS 数据)。"""
    monkeypatch.setattr(miner, "MINING_WINDOWS", _SHORT_WINDOWS)


def test_mine_factors_runs_writes_memory_entry_and_returns_result(session, short_windows):
    provider = _SyntheticProvider()
    gemini = _FakeGeminiMomentum60()

    results = mine_factors(session, provider, gemini, n=1)

    assert len(results) == 1
    result = results[0]
    assert result["factor"] == "momentum"
    assert result["params"] == {"window": 60}
    assert result["verdict"] in ("validated", "no_improvement", "refuted")

    rows = get_entries(session, kind="factor")
    agent_rows = [r for r in rows if r.source == "agent"]
    assert len(agent_rows) == 1
    row = agent_rows[0]
    assert row.status == result["verdict"]
    assert "momentum" in row.title

    evidence = json.loads(row.evidence_json)
    assert evidence["verdict"] == result["verdict"]
    assert set(evidence["windows"].keys()) == {"tiny_a", "tiny_b"}
    assert evidence["proposal"]["factor"] == "momentum"


def test_mine_factors_second_run_avoids_the_previously_tried_factor(session, short_windows):
    provider = _SyntheticProvider()

    first = mine_factors(session, provider, _FakeGeminiMomentum60(), n=1)
    assert len(first) == 1

    class _GeminiRepeatsMomentum:
        def generate_json(self, prompt):
            assert "momentum" in prompt  # avoid 名单真的进了 prompt
            # 故意仍只提议已试过的 momentum——应该被 avoid 过滤掉。
            return {"proposals": [
                {"factor": "momentum", "params": {"window": 60}, "hypothesis": "repeat"},
            ]}

    second = mine_factors(session, provider, _GeminiRepeatsMomentum(), n=1)
    assert second == []  # 唯一提案被 avoid 过滤,没有可挖掘的候选

    rows = [r for r in get_entries(session, kind="factor") if r.source == "agent"]
    assert len(rows) == 1  # 没有新增


def test_mine_factors_catches_per_proposal_backtest_errors_and_continues(session, short_windows,
                                                                          monkeypatch):
    provider = _SyntheticProvider()

    def _boom(name, params):
        raise RuntimeError("simulated backtest blowup")

    monkeypatch.setattr(miner, "build_factor", _boom)

    results = mine_factors(session, provider, _FakeGeminiMomentum60(), n=1)

    assert len(results) == 1
    assert results[0]["verdict"] == "error"
    assert "error" in results[0]
    # 出错的提案不写知识库条目。
    assert get_entries(session, kind="factor") == []


def test_mine_factors_gemini_none_still_produces_results_from_seeds(session, short_windows):
    provider = _SyntheticProvider()
    results = mine_factors(session, provider, None, n=1)
    assert len(results) == 1
    assert results[0]["factor"] in ("momentum", "low_volatility", "rsi_strength")
