"""市场状态(regime)可复用服务:大盘(SPY)相对其 200 日均线的风险开关状态。

复用 app/backtest/regime_signal.py 的纯规则信号(无 LLM)。原先这段计算 INLINE
在 app/api/routes_market.py 的路由体里,现在抽到这里,给两类消费方共用同一份
逻辑:
- app/api/routes_market.py 的只读展示路由(GET /api/market/regime);
- app/services/committee_service.py 的委员会 prompt 宏观背景(见下面
  regime_context_line)——ADVISORY CONTEXT ONLY,只喂进 LLM 提示词,绝不进
  RiskGate/下单路径。调用方(trade_cycle_service/picks_service/routes_stock)
  在各自"一轮/一次请求"的作用域里只调一次 get_regime 并复用给所有标的,不是
  每只标的各抓一次 SPY(SPY 是大盘背景,与具体标的无关,没必要重复抓取)。

取价失败(provider 抛错/网络问题/无 SPY 数据)一律降级为 available=False,绝不
抛异常——两类消费方都不能因为 SPY 数据缺失而被拖垮。
"""
import datetime as dt

from app.backtest.regime_signal import risk_on_asof
from app.data.base import PriceProvider
from app.services.market_data_service import fetch_bars
from app.util.trading_day import et_trading_day

_UNAVAILABLE = {
    "available": False,
    "risk_on": None,
    "spy_close": None,
    "spy_sma200": None,
    "distance_pct": None,
}


def get_regime(price_provider: PriceProvider, as_of: dt.date | None = None) -> dict:
    """算一次大盘 regime。as_of 缺省时用 et_trading_day(now UTC)。

    返回 {as_of, available, risk_on, spy_close, spy_sma200, distance_pct}。
    SPY 数据缺失/抓取异常一律降级为 available=False(其余字段 None),绝不
    抛异常——调用方(只读路由/委员会编排)都不需要自己再包一层 try/except
    应付"取不到 SPY"这一种情况(但仍建议在自己的调用点外包一层防御,见
    trade_cycle_service/picks_service/routes_stock 的调用点注释)。
    """
    as_of = as_of or et_trading_day(dt.datetime.now(dt.UTC))

    try:
        bars, _skipped = fetch_bars(
            price_provider, ["SPY"], as_of - dt.timedelta(days=420), as_of
        )
    except Exception:
        # 取价失败(provider 抛错/网络问题)一律降级为无数据,绝不向上抛异常。
        bars = {}

    spy = bars.get("SPY")
    if spy is None or spy.empty:
        return {"as_of": as_of.isoformat(), **_UNAVAILABLE}

    risk_on = risk_on_asof(spy, as_of, 200)
    closes = spy["close"][spy.index.date <= as_of]
    spy_close = float(closes.iloc[-1])
    spy_sma200 = float(closes.iloc[-200:].mean()) if len(closes) >= 200 else None
    distance_pct = (
        (spy_close - spy_sma200) / spy_sma200 * 100 if spy_sma200 else None
    )

    return {
        "as_of": as_of.isoformat(),
        "available": True,
        "risk_on": risk_on,
        "spy_close": round(spy_close, 2),
        "spy_sma200": round(spy_sma200, 2) if spy_sma200 is not None else None,
        "distance_pct": round(distance_pct, 2) if distance_pct is not None else None,
    }


def regime_context_line(regime: dict) -> str:
    """把 get_regime() 的结果拼成一行给委员会 prompt 用的中文宏观背景提示。

    ADVISORY CONTEXT ONLY:这行文字只会被 committee_service.run_committee 的
    market_context 参数拼进 LLM 提示词,不改变任何下单/风控判定(见
    tests/test_memory_advisory_isolation.py 同款红线,对 memory_context 已有的
    静态扫描 + import 图校验)。

    regime 不可用、或 risk_on/关键数值缺失(均线尚未成形等边界情况)→ 返回
    空字符串,调用方据此整节从 prompt 省略,绝不用 None 去格式化字符串。
    """
    if not regime.get("available") or regime.get("risk_on") is None:
        return ""
    spy_close = regime.get("spy_close")
    spy_sma200 = regime.get("spy_sma200")
    distance_pct = regime.get("distance_pct")
    if spy_close is None or spy_sma200 is None or distance_pct is None:
        return ""
    if regime["risk_on"]:
        return (
            f"当前大盘处于 risk-on:SPY({spy_close}) 在 200 日均线({spy_sma200})"
            f"上方 ({distance_pct:+.1f}%),系统性风险偏低。"
        )
    return (
        f"当前大盘处于 risk-off:SPY({spy_close}) 跌破 200 日均线({spy_sma200}) "
        f"({distance_pct:+.1f}%),存在系统性下行风险,对新买入应更谨慎。"
    )
