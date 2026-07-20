"""app.cli 的 mine-factors 子命令:装配薄壳测试(注入 fake session/provider/
gemini,离线,短窗口保持快)。编排逻辑本身在 tests/factors/test_miner.py 已
覆盖,这里只测 CLI 参数解析 + 装配 + 摘要打印。
"""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from app.cli import build_parser, cmd_mine_factors
from app.data.base import PriceProvider, empty_bars
from app.factors import miner
from app.store.db import init_db, make_engine, make_session_factory

_SHORT_WINDOWS = [
    ("tiny_a", dt.date(2024, 1, 2), dt.date(2024, 1, 19)),
    ("tiny_b", dt.date(2024, 2, 1), dt.date(2024, 2, 16)),
]


def _stable_seed(symbol: str) -> int:
    return sum(ord(c) for c in symbol)


class _FakeProvider(PriceProvider):
    def get_daily_bars(self, symbol, start, end):
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
             "close": close, "volume": 1_000_000.0},
            index=idx,
        )


class _FakeGeminiMomentum:
    def generate_json(self, prompt):
        return {"proposals": [
            {"factor": "momentum", "params": {"window": 60}, "hypothesis": "test"},
        ]}


@pytest.fixture
def session():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


@pytest.fixture(autouse=True)
def _short_windows(monkeypatch):
    monkeypatch.setattr(miner, "MINING_WINDOWS", _SHORT_WINDOWS)


def test_build_parser_mine_factors_defaults_n_to_three():
    args = build_parser().parse_args(["mine-factors"])
    assert args.command == "mine-factors"
    assert args.n == 3


def test_build_parser_mine_factors_accepts_custom_n():
    args = build_parser().parse_args(["mine-factors", "--n", "2"])
    assert args.n == 2


def test_cmd_mine_factors_prints_factor_and_verdict(session, capsys):
    args = build_parser().parse_args(["mine-factors", "--n", "1"])
    rc = cmd_mine_factors(args, session=session, provider=_FakeProvider(),
                          gemini_client=_FakeGeminiMomentum())
    assert rc == 0
    out = capsys.readouterr().out
    assert "momentum" in out
    assert "verdict=" in out


def test_cmd_mine_factors_works_with_gemini_client_none(session, capsys):
    args = build_parser().parse_args(["mine-factors", "--n", "1"])
    rc = cmd_mine_factors(args, session=session, provider=_FakeProvider(), gemini_client=None)
    assert rc == 0
    out = capsys.readouterr().out
    assert "因子挖掘" in out
