import math
from collections import defaultdict, deque

import pandas as pd

TRADING_DAYS_PER_YEAR = 252


def total_return(equity: pd.Series) -> float:
    return float(equity.iloc[-1] / equity.iloc[0] - 1)


def max_drawdown(equity: pd.Series) -> float:
    """最大回撤,负数(如 -0.25 表示回撤 25%)。"""
    return float((equity / equity.cummax() - 1).min())


def sharpe(equity: pd.Series) -> float:
    """日收益年化 Sharpe(无风险利率按 0)。零波动或样本不足返回 0。"""
    r = equity.pct_change().dropna()
    if len(r) < 2:
        return 0.0
    sd = r.std()
    if sd == 0 or math.isnan(sd):
        return 0.0
    return float(r.mean() / sd * math.sqrt(TRADING_DAYS_PER_YEAR))


def round_trips(fills) -> list:
    """FIFO 配对买卖,返回每笔卖出的已实现盈亏。"""
    lots = defaultdict(deque)  # symbol -> deque of [shares, buy_price]
    pnls = []
    for f in fills:
        if f.side == "buy":
            lots[f.symbol].append([f.shares, f.price])
            continue
        remaining = f.shares
        pnl = 0.0
        queue = lots[f.symbol]
        while remaining > 0 and queue:
            lot = queue[0]
            take = min(lot[0], remaining)
            pnl += take * (f.price - lot[1])
            lot[0] -= take
            remaining -= take
            if lot[0] == 0:
                queue.popleft()
        pnls.append(pnl)
    return pnls


def win_rate(fills) -> float:
    pnls = round_trips(fills)
    if not pnls:
        return 0.0
    return sum(1 for p in pnls if p > 0) / len(pnls)
