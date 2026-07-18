import datetime as dt

from app.data.base import PriceProvider
from app.screener.base import Screener
from app.screener.rules_momentum import MomentumRule
from app.screener.rules_trend import TrendRule
from app.screener.rules_volume import VolumeRule


def default_screener() -> Screener:
    """默认权重:趋势 0.4 / 动量 0.4 / 量能 0.2。"""
    return Screener([(TrendRule(), 0.4), (MomentumRule(), 0.4), (VolumeRule(), 0.2)])


def run_screen(provider: PriceProvider, symbols, top_n: int, lookback_days: int, as_of: dt.date):
    screener = default_screener()
    start = as_of - dt.timedelta(days=lookback_days)
    bars = {sym: provider.get_daily_bars(sym, start, as_of) for sym in symbols}
    return screener.rank(bars, top_n)
