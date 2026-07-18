import pytest

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
