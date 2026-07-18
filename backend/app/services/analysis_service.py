import datetime as dt

from app.data.base import PriceProvider
from app.screener.base import Screener
from app.screener.rules_momentum import MomentumRule
from app.screener.rules_trend import TrendRule
from app.screener.rules_volume import VolumeRule
from app.services.market_data_service import fetch_bars


def default_screener() -> Screener:
    """默认权重:趋势 0.4 / 动量 0.4 / 量能 0.2。"""
    return Screener([(TrendRule(), 0.4), (MomentumRule(), 0.4), (VolumeRule(), 0.2)])


def run_screen_on_bars(bars_by_symbol: dict, top_n: int) -> list:
    """对已抓好的 bars 直接打分排序,不做任何抓取。"""
    return default_screener().rank(bars_by_symbol, top_n)


def run_screen(provider: PriceProvider, symbols, top_n: int, lookback_days: int, as_of: dt.date) -> list:
    """薄封装:经 market_data_service 逐标的抓取(单只失败不影响其余),再委托打分。"""
    start = as_of - dt.timedelta(days=lookback_days)
    bars, _skipped = fetch_bars(provider, symbols, start, as_of)
    return run_screen_on_bars(bars, top_n)
