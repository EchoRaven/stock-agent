"""美东交易日纯函数。全系统 as_of 一律用 ET 日历日,终结 host-local date 时区耦合。"""
import datetime as dt
from zoneinfo import ZoneInfo

ET_ZONE = ZoneInfo("America/New_York")


def et_trading_day(now_utc: dt.datetime) -> dt.date:
    """UTC 时刻 → 美东日历日。naive 输入按 UTC 解释;时间由调用方注入,便于测试。"""
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=dt.UTC)
    return now_utc.astimezone(ET_ZONE).date()
