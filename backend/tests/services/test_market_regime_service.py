"""app.services.market_regime_service —— SPY vs 200 日均线 regime 的可复用服务。
全离线(fake 行情源,不发起任何真实网络请求)。与 tests/api/test_market.py 的
fake provider 风格一致,但直接调 get_regime/regime_context_line(不经 HTTP
层)——这个服务模块是从 routes_market.py 的路由体里抽出来的,routes 测试保证
"路由行为不变",这里保证"服务本身的逻辑覆盖 + 委员会用的宏观文案生成"。
"""
import datetime as dt

import numpy as np
import pandas as pd

from app.data.base import PriceProvider, empty_bars
from app.services.market_regime_service import get_regime, regime_context_line

AS_OF = dt.date(2026, 7, 17)


class _SpyProvider(PriceProvider):
    """'rising'/'declining' 构造单调 SPY 序列(避免均线临界值抖动);'empty' 模拟
    无 SPY 数据;'raise' 模拟 provider 整体故障。"""

    def __init__(self, mode: str):
        self._mode = mode

    def get_daily_bars(self, symbol: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        if self._mode == "raise":
            raise RuntimeError("provider unavailable")
        if self._mode == "empty" or symbol != "SPY" or start > end:
            return empty_bars()
        idx = pd.date_range(start, end, freq="D")
        n = len(idx)
        if self._mode == "rising":
            closes = 300.0 + 0.5 * np.arange(n)
        elif self._mode == "declining":
            closes = 500.0 - 0.5 * np.arange(n)
        else:
            raise ValueError(f"unknown mode {self._mode}")
        return pd.DataFrame(
            {"open": closes, "high": closes, "low": closes, "close": closes,
             "volume": 1_000_000.0},
            index=idx,
        )


# ---------------------------------------------------------------------------
# get_regime
# ---------------------------------------------------------------------------


def test_get_regime_rising_spy_is_risk_on_with_positive_distance():
    regime = get_regime(_SpyProvider("rising"), AS_OF)
    assert regime["as_of"] == AS_OF.isoformat()
    assert regime["available"] is True
    assert regime["risk_on"] is True
    assert regime["spy_close"] is not None
    assert regime["spy_sma200"] is not None
    assert regime["distance_pct"] > 0
    assert regime["spy_close"] > regime["spy_sma200"]


def test_get_regime_declining_spy_is_risk_off_with_negative_distance():
    regime = get_regime(_SpyProvider("declining"), AS_OF)
    assert regime["available"] is True
    assert regime["risk_on"] is False
    assert regime["distance_pct"] < 0
    assert regime["spy_close"] < regime["spy_sma200"]


def test_get_regime_no_spy_data_degrades_to_unavailable():
    regime = get_regime(_SpyProvider("empty"), AS_OF)
    assert regime == {
        "as_of": AS_OF.isoformat(),
        "available": False,
        "risk_on": None,
        "spy_close": None,
        "spy_sma200": None,
        "distance_pct": None,
    }


def test_get_regime_provider_raising_degrades_gracefully():
    regime = get_regime(_SpyProvider("raise"), AS_OF)
    assert regime["available"] is False
    assert regime["risk_on"] is None
    assert regime["spy_close"] is None
    assert regime["spy_sma200"] is None
    assert regime["distance_pct"] is None


def test_get_regime_defaults_as_of_when_omitted():
    # as_of 缺省 → 用 et_trading_day(now UTC),不需要传参也不抛异常。
    regime = get_regime(_SpyProvider("empty"))
    assert "as_of" in regime
    assert regime["available"] is False


# ---------------------------------------------------------------------------
# regime_context_line —— 委员会 prompt 用的中文宏观提示行
# ---------------------------------------------------------------------------


def test_regime_context_line_risk_on_mentions_risk_on_and_numbers():
    regime = get_regime(_SpyProvider("rising"), AS_OF)
    line = regime_context_line(regime)
    assert "risk-on" in line
    assert str(regime["spy_close"]) in line
    assert str(regime["spy_sma200"]) in line


def test_regime_context_line_risk_off_mentions_risk_off_and_caution():
    regime = get_regime(_SpyProvider("declining"), AS_OF)
    line = regime_context_line(regime)
    assert "risk-off" in line
    assert "谨慎" in line


def test_regime_context_line_unavailable_is_empty():
    regime = get_regime(_SpyProvider("empty"), AS_OF)
    assert regime_context_line(regime) == ""


def test_regime_context_line_provider_raising_is_empty():
    regime = get_regime(_SpyProvider("raise"), AS_OF)
    assert regime_context_line(regime) == ""


def test_regime_context_line_risk_on_none_is_empty():
    assert regime_context_line({"available": True, "risk_on": None}) == ""


def test_regime_context_line_missing_keys_is_empty():
    assert regime_context_line({}) == ""
