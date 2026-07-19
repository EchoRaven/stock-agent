"""Point-in-time S&P 500 成分股加载(research 用,消除人工选股偏差)。
数据:backend/data/sp500_pit_2019plus.csv(date,tickers 全成分,含后来被剔除者)。
注意:免费价格源(yfinance)缺退市/更名股价格数据 → 本加载只解决"选股偏差",
不解决"退市幸存者偏差";回测时须报告不可抓取(退市)比例作残余偏差。"""
import csv
import datetime as dt
from pathlib import Path

_CSV = Path(__file__).resolve().parent.parent.parent / "data" / "sp500_pit_2019plus.csv"


def _load_rows(path: Path = None) -> list:
    p = path or _CSV
    rows = []
    with open(p, newline="") as f:
        for r in csv.DictReader(f):
            rows.append((dt.date.fromisoformat(r["date"]), [t.strip().upper() for t in r["tickers"].split(",") if t.strip()]))
    rows.sort(key=lambda x: x[0])
    return rows


def constituents_asof(as_of: dt.date, path: Path = None) -> list:
    """返回 <= as_of 最近一个成分日期的成分股列表(去重、保序)。无匹配→[]。"""
    rows = _load_rows(path)
    best = []
    for d, tickers in rows:
        if d <= as_of:
            best = tickers
        else:
            break
    # 去重保序
    seen = set(); out = []
    for t in best:
        if t not in seen:
            seen.add(t); out.append(t)
    return out
