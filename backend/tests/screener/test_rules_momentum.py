import pytest

from app.screener.rules_momentum import MomentumRule, rsi_band_score
from tests.helpers import make_bars


def test_rsi_band_score_piecewise():
    assert rsi_band_score(25) == 0.0
    assert rsi_band_score(40) == pytest.approx(0.5)
    assert rsi_band_score(60) == 1.0
    assert rsi_band_score(75) == pytest.approx(0.5)
    assert rsi_band_score(90) == 0.0


def test_insufficient_data_scores_zero():
    out = MomentumRule().evaluate(make_bars(days=10))
    assert out.score == 0.0
    assert "insufficient" in out.detail


def test_uptrend_beats_downtrend():
    up = MomentumRule().evaluate(make_bars(days=60, base=100.0, step=1.0))
    # step=-5 保证 20 日收益 < -10%,ret_score 与 RSI 区间分都到 0
    down = MomentumRule().evaluate(make_bars(days=60, base=500.0, step=-5.0))
    assert up.score > down.score
    assert down.score == pytest.approx(0.0)
