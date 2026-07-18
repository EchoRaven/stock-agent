import math

import pytest

from app.screener.base import Screener
from app.screener.rules_volume import VolumeRule
from tests.helpers import make_bars


def test_constant_volume_scores_one_third():
    out = VolumeRule().evaluate(make_bars(days=80))
    assert out.score == pytest.approx((1.0 - 0.5) / 1.5, abs=1e-6)


def test_volume_surge_scores_full():
    bars = make_bars(days=80)
    bars.iloc[-5:, bars.columns.get_loc("volume")] = 10_000_000.0
    assert VolumeRule().evaluate(bars).score == 1.0


def test_insufficient_data_scores_zero():
    out = VolumeRule().evaluate(make_bars(days=30))
    assert out.score == 0.0
    assert "insufficient" in out.detail


def test_zero_volume_scores_zero():
    """成交量全为 0(如新股/停牌数据错填)时 v60==0,不得除零。"""
    bars = make_bars(days=80, volume=0.0)
    out = VolumeRule().evaluate(bars)
    assert out.score == 0.0
    assert out.detail == "no volume"


def test_all_nan_volume_tail_keeps_screener_total_finite():
    """量能尾部全 NaN → v5/v60 都是 NaN,VolumeRule 内部已用 clamp01,
    加上 Item 3(clamp01 NaN->0)后规则分与 Screener 总分都保持有限值(0),
    而不是把 NaN 一路带进 Screener.rank() 破坏排序。"""
    bars = make_bars(days=80)
    vol_col = bars.columns.get_loc("volume")
    bars.iloc[-60:, vol_col] = float("nan")

    rule_out = VolumeRule().evaluate(bars)
    assert rule_out.score == 0.0

    screener = Screener([(VolumeRule(), 1.0)])
    scored = screener.score_symbol("X", bars)
    assert math.isfinite(scored.total)
    assert scored.total == 0.0
