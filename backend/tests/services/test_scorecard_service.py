"""app.services.scorecard_service —— 决策记分卡:委员会推荐是否有区分度的纯
聚合测量。全离线(in-memory SQLite,不发起任何网络请求),直接调
build_scorecard(不经 HTTP 层);路由层行为在 tests/api/test_history.py 里覆盖。
"""
import datetime as dt
import statistics

import pandas as pd
import pytest

from app.data.base import PriceProvider, empty_bars
from app.services.scorecard_service import (MIN_SIGNAL_DAYS, MIN_SIGNAL_N,
                                            build_forward_returns, build_scorecard)
from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.decision_repo import save_decision
from app.store.repos.order_repo import create_order

AS_OF = dt.date(2026, 7, 10)


@pytest.fixture
def session():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


def _seed(session, action, confidence, as_of=AS_OF, symbol="AAPL", mode="advisory"):
    save_decision(session, as_of, symbol, action, confidence, mode, "{}")


# ---------------------------------------------------------------------------
# buy-heavy + flat-confidence set
# ---------------------------------------------------------------------------

def test_buy_heavy_flat_confidence_flags_fire(session):
    # 10 buy (7 @ 0.85, 3 @ 0.90) + 2 hold @ 0.85 = 12 total, 0 sell.
    for i in range(7):
        _seed(session, "buy", 0.85, symbol=f"B{i}")
    for i in range(3):
        _seed(session, "buy", 0.90, symbol=f"B{7 + i}")
    for i in range(2):
        _seed(session, "hold", 0.85, symbol=f"H{i}")
    session.commit()

    result = build_scorecard(session)

    assert result["total"] == 12
    assert result["by_action"] == {"buy": 10, "sell": 0, "hold": 2}
    assert result["by_action_pct"]["buy"] == round(10 / 12, 3)
    assert result["by_action_pct"]["sell"] == 0.0

    confidences = [0.85] * 7 + [0.90] * 3 + [0.85] * 2
    assert result["confidence"]["n"] == 12
    assert result["confidence"]["mean"] == round(statistics.mean(confidences), 3)
    assert result["confidence"]["median"] == round(statistics.median(confidences), 3)
    assert result["confidence"]["min"] == 0.85
    assert result["confidence"]["max"] == 0.90
    assert result["confidence"]["stdev"] == round(statistics.stdev(confidences), 3)

    codes = {f["code"] for f in result["flags"]}
    assert {"buy_heavy", "no_sells", "flat_confidence"} <= codes
    assert "calibration_ok" not in codes  # negative flags found -> not "ok"

    buy_heavy = next(f for f in result["flags"] if f["code"] == "buy_heavy")
    assert buy_heavy["severity"] == "warn"
    assert "83.3%" in buy_heavy["message"]

    flat = next(f for f in result["flags"] if f["code"] == "flat_confidence")
    assert flat["severity"] == "warn"

    no_sells = next(f for f in result["flags"] if f["code"] == "no_sells")
    assert no_sells["severity"] == "warn"


# ---------------------------------------------------------------------------
# balanced set with spread confidence
# ---------------------------------------------------------------------------

def test_balanced_spread_confidence_no_warn_flags(session):
    buy_conf = [0.9, 0.8, 0.7, 0.6]
    sell_conf = [0.5, 0.4, 0.3, 0.2]
    hold_conf = [0.95, 0.55, 0.45, 0.25]
    for i, c in enumerate(buy_conf):
        _seed(session, "buy", c, symbol=f"B{i}")
    for i, c in enumerate(sell_conf):
        _seed(session, "sell", c, symbol=f"S{i}")
    for i, c in enumerate(hold_conf):
        _seed(session, "hold", c, symbol=f"H{i}")
    session.commit()

    result = build_scorecard(session)

    assert result["total"] == 12
    assert result["by_action"] == {"buy": 4, "sell": 4, "hold": 4}

    codes = {f["code"] for f in result["flags"]}
    assert codes == {"calibration_ok"}
    ok = result["flags"][0]
    assert ok["code"] == "calibration_ok"
    assert ok["severity"] == "info"


# ---------------------------------------------------------------------------
# empty DB
# ---------------------------------------------------------------------------

def test_empty_db_returns_zeros_and_insufficient_data(session):
    result = build_scorecard(session)

    assert result["total"] == 0
    assert result["distinct_symbols"] == 0
    assert result["as_of_from"] is None
    assert result["as_of_to"] is None
    assert result["by_action"] == {"buy": 0, "sell": 0, "hold": 0}
    assert result["by_action_pct"] == {"buy": 0.0, "sell": 0.0, "hold": 0.0}
    assert result["confidence"] == {
        "n": 0, "mean": None, "median": None, "min": None, "max": None, "stdev": None,
    }
    assert sum(b["count"] for b in result["histogram"]) == 0
    assert result["flags"] == [{
        "code": "insufficient_data",
        "severity": "info",
        "message": "决策样本不足(0 条,至少需要 10 条)——暂不做校准判断",
    }]


# ---------------------------------------------------------------------------
# fewer than 10 decisions
# ---------------------------------------------------------------------------

def test_fewer_than_ten_decisions_insufficient_data_only(session):
    # 9 buys at flat high confidence -- would trigger buy_heavy/flat_confidence/
    # confidence_floor/no_sells if the 10-row gate weren't in place.
    for i in range(9):
        _seed(session, "buy", 0.9, symbol=f"B{i}")
    session.commit()

    result = build_scorecard(session)

    assert result["total"] == 9
    codes = [f["code"] for f in result["flags"]]
    assert codes == ["insufficient_data"]


# ---------------------------------------------------------------------------
# histogram bucket boundaries
# ---------------------------------------------------------------------------

def test_histogram_buckets_bin_lower_inclusive(session):
    _seed(session, "buy", 0.45, symbol="A")   # <0.5
    _seed(session, "buy", 0.55, symbol="B")   # 0.5-0.6
    _seed(session, "buy", 0.65, symbol="C")   # 0.6-0.7
    _seed(session, "buy", 0.70, symbol="D")   # boundary -> 0.7-0.8 (lower-inclusive)
    _seed(session, "buy", 0.85, symbol="E")   # 0.8-0.9
    _seed(session, "buy", 0.90, symbol="F")   # boundary -> 0.9-1.0 (lower-inclusive)
    _seed(session, "buy", 1.0, symbol="G")    # top of last bucket
    session.commit()

    result = build_scorecard(session)
    by_bucket = {b["bucket"]: b["count"] for b in result["histogram"]}

    assert by_bucket["<0.5"] == 1
    assert by_bucket["0.5–0.6"] == 1
    assert by_bucket["0.6–0.7"] == 1
    assert by_bucket["0.7–0.8"] == 1  # the 0.70 boundary value
    assert by_bucket["0.8–0.9"] == 1
    assert by_bucket["0.9–1.0"] == 2  # the 0.90 boundary value + 1.0
    assert [b["bucket"] for b in result["histogram"]] == [
        "<0.5", "0.5–0.6", "0.6–0.7", "0.7–0.8", "0.8–0.9", "0.9–1.0",
    ]


# ---------------------------------------------------------------------------
# days window
# ---------------------------------------------------------------------------

def test_days_window_filters_older_decisions(session):
    _seed(session, "buy", 0.8, as_of=dt.date(2026, 7, 1), symbol="OLD")
    _seed(session, "buy", 0.8, as_of=dt.date(2026, 7, 18), symbol="RECENT")
    session.commit()

    now_utc = dt.datetime(2026, 7, 20, 16, 0, tzinfo=dt.UTC)  # ET 2026-07-20 noon
    result = build_scorecard(session, days=5, now_utc=now_utc)

    assert result["total"] == 1
    assert result["window_days"] == 5
    assert result["as_of_from"] == "2026-07-18"
    assert result["as_of_to"] == "2026-07-18"

    result_all = build_scorecard(session, now_utc=now_utc)
    assert result_all["total"] == 2
    assert result_all["window_days"] is None


# ---------------------------------------------------------------------------
# gate (order-status counts)
# ---------------------------------------------------------------------------

def test_gate_counts_orders_by_status_zero_filled(session):
    create_order(session, AS_OF, "AAPL", "buy", 10, "rejected", "advisory", reason="cap")
    create_order(session, AS_OF, "MSFT", "buy", 5, "filled", "advisory")
    create_order(session, AS_OF, "MSFT", "sell", 5, "filled", "advisory")
    session.commit()

    result = build_scorecard(session)

    assert result["gate"] == {
        "pending_confirmation": 0,
        "approved": 0,
        "rejected": 1,
        "submitted": 0,
        "filled": 2,
        "cancelled": 0,
    }


# ---------------------------------------------------------------------------
# forward returns — do the decisions pay off? (offline fake price provider,
# deterministic bars, hand-computed expected returns)
# ---------------------------------------------------------------------------

FR_AS_OF = dt.date(2026, 7, 1)
# 9 sequential bars; index 4 == FR_AS_OF (2026-06-25 .. 2026-07-07).
FR_DATES = ["2026-06-25", "2026-06-26", "2026-06-29", "2026-06-30", "2026-07-01",
           "2026-07-02", "2026-07-03", "2026-07-06", "2026-07-07"]
FR_NOW_UTC = dt.datetime(2026, 7, 8, 16, 0, tzinfo=dt.UTC)


def _bars(closes: list[float]) -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in FR_DATES])
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes,
         "volume": [1_000_000.0] * len(closes)},
        index=idx,
    )


class FixedBarsProvider(PriceProvider):
    """离线测试行情源:每个 symbol 固定一段收盘价序列(9 根 bar,idx4==FR_AS_OF);
    fail/empty 集合模拟抓取异常/空数据,验证逐 symbol 隔离不互相影响。"""

    def __init__(self, bars_map: dict | None = None,
                fail: set | None = None, empty: set | None = None):
        self._map = bars_map or {}
        self._fail = fail or set()
        self._empty = empty or set()

    def get_daily_bars(self, symbol, start, end):
        if symbol in self._fail:
            raise RuntimeError("boom")
        if symbol in self._empty:
            return empty_bars()
        return self._map.get(symbol, empty_bars())


def test_forward_returns_matured_buys_rose_exact_stats(session):
    # entry = idx4 (FR_AS_OF), h=1 exit = idx5.
    # BUY_A: 104 -> 105 => +0.9615384615...% -> round3 0.962
    # BUY_B: 58  -> 60  => +3.4482758620...% -> round3 3.448
    save_decision(session, FR_AS_OF, "BUY_A", "buy", 0.8, "advisory", "{}")
    save_decision(session, FR_AS_OF, "BUY_B", "buy", 0.8, "advisory", "{}")
    session.commit()

    provider = FixedBarsProvider(bars_map={
        "BUY_A": _bars([100, 101, 102, 103, 104, 105, 106, 107, 108]),
        "BUY_B": _bars([50, 52, 54, 56, 58, 60, 62, 64, 66]),
    })

    result = build_forward_returns(session, provider, horizons=(1, 5, 20), now_utc=FR_NOW_UTC)

    h1 = result["by_horizon"]["1"]
    assert h1["coverage"] == {"matured": 2, "pending": 0, "unpriced": 0}
    buy = h1["by_action"]["buy"]
    assert buy["n"] == 2
    assert buy["mean_return_pct"] == pytest.approx(2.205)
    assert buy["median_return_pct"] == pytest.approx(2.205)
    assert buy["hit_rate"] == 1.0
    assert buy["hit_rate_meaning"] == "涨了算对"
    # sell/hold present with all-None stats (no such decisions this horizon)
    assert h1["by_action"]["sell"]["n"] == 0
    assert h1["by_action"]["sell"]["mean_return_pct"] is None
    assert h1["by_action"]["hold"]["n"] == 0

    # too few bars remain after entry for h=5/h=20 -> pending, NOT matured/zero
    for h_key in ("5", "20"):
        block = result["by_horizon"][h_key]
        assert block["coverage"] == {"matured": 0, "pending": 2, "unpriced": 0}
        assert block["by_action"]["buy"] == {
            "n": 0, "mean_return_pct": None, "median_return_pct": None,
            "hit_rate": None, "hit_rate_meaning": "涨了算对",
        }


def test_forward_returns_sell_hit_rate_when_price_fell(session):
    # entry idx4=104 -> exit idx5=103 => -0.9615384615...% -> round3 -0.962 (<0 -> sell "right")
    save_decision(session, FR_AS_OF, "SELL_A", "sell", 0.7, "advisory", "{}")
    session.commit()

    provider = FixedBarsProvider(bars_map={
        "SELL_A": _bars([108, 107, 106, 105, 104, 103, 102, 101, 100]),
    })

    result = build_forward_returns(session, provider, horizons=(1,), now_utc=FR_NOW_UTC)

    sell = result["by_horizon"]["1"]["by_action"]["sell"]
    assert sell["n"] == 1
    assert sell["mean_return_pct"] == pytest.approx(-0.962)
    assert sell["hit_rate"] == 1.0
    assert sell["hit_rate_meaning"] == "跌了算对,即避开了损失"


def test_forward_returns_hold_never_reports_hit_rate(session):
    save_decision(session, FR_AS_OF, "HOLD_A", "hold", 0.6, "advisory", "{}")
    session.commit()

    provider = FixedBarsProvider(bars_map={
        "HOLD_A": _bars([100, 101, 102, 103, 104, 105, 106, 107, 108]),
    })

    result = build_forward_returns(session, provider, horizons=(1,), now_utc=FR_NOW_UTC)

    hold = result["by_horizon"]["1"]["by_action"]["hold"]
    assert hold["n"] == 1
    assert hold["mean_return_pct"] == pytest.approx(0.962)
    assert hold["hit_rate"] is None  # hold makes no directional claim
    assert hold["hit_rate_meaning"] == "不作方向性判断,不计 hit_rate"


def test_forward_returns_unpriced_symbol_isolated_no_crash(session):
    save_decision(session, FR_AS_OF, "BUY_A", "buy", 0.8, "advisory", "{}")
    save_decision(session, FR_AS_OF, "FAIL_SYM", "buy", 0.8, "advisory", "{}")
    save_decision(session, FR_AS_OF, "EMPTY_SYM", "buy", 0.8, "advisory", "{}")
    session.commit()

    provider = FixedBarsProvider(
        bars_map={"BUY_A": _bars([100, 101, 102, 103, 104, 105, 106, 107, 108])},
        fail={"FAIL_SYM"}, empty={"EMPTY_SYM"},
    )

    result = build_forward_returns(session, provider, horizons=(1,), now_utc=FR_NOW_UTC)

    h1 = result["by_horizon"]["1"]
    assert h1["coverage"] == {"matured": 1, "pending": 0, "unpriced": 2}
    assert h1["by_action"]["buy"]["n"] == 1
    assert h1["by_action"]["buy"]["mean_return_pct"] == pytest.approx(0.962)


def test_forward_returns_buy_by_confidence_buckets_and_lists_empty_buckets(session):
    # entry idx4=100 always; exit idx5 chosen for a clean round return.
    save_decision(session, FR_AS_OF, "C_LOW", "buy", 0.45, "advisory", "{}")   # <0.5
    save_decision(session, FR_AS_OF, "C_MID", "buy", 0.75, "advisory", "{}")  # 0.7-0.8
    save_decision(session, FR_AS_OF, "C_HIGH", "buy", 0.95, "advisory", "{}")  # 0.9-1.0
    session.commit()

    provider = FixedBarsProvider(bars_map={
        "C_LOW": _bars([100, 100, 100, 100, 100, 101, 101, 101, 101]),   # +1.0%
        "C_MID": _bars([100, 100, 100, 100, 100, 104, 104, 104, 104]),   # +4.0%
        "C_HIGH": _bars([100, 100, 100, 100, 100, 106, 106, 106, 106]),  # +6.0%
    })

    result = build_forward_returns(session, provider, horizons=(1,), now_utc=FR_NOW_UTC)

    buckets = {b["bucket"]: b for b in result["by_horizon"]["1"]["buy_by_confidence"]}
    assert buckets["<0.5"] == {"bucket": "<0.5", "n": 1, "mean_return_pct": 1.0, "hit_rate": 1.0}
    assert buckets["0.7–0.8"] == {"bucket": "0.7–0.8", "n": 1, "mean_return_pct": 4.0, "hit_rate": 1.0}
    assert buckets["0.9–1.0"] == {"bucket": "0.9–1.0", "n": 1, "mean_return_pct": 6.0, "hit_rate": 1.0}
    # untouched buckets still listed (never dropped), stats None not 0
    for empty_label in ("0.5–0.6", "0.6–0.7", "0.8–0.9"):
        assert buckets[empty_label] == {
            "bucket": empty_label, "n": 0, "mean_return_pct": None, "hit_rate": None,
        }
    assert [b["bucket"] for b in result["by_horizon"]["1"]["buy_by_confidence"]] == [
        "<0.5", "0.5–0.6", "0.6–0.7", "0.7–0.8", "0.8–0.9", "0.9–1.0",
    ]


def test_forward_returns_confidence_signal_below_min_n_no_conclusion(session):
    for i in range(5):  # well under MIN_SIGNAL_N (20)
        save_decision(session, FR_AS_OF, f"SIG{i}", "buy", 0.5 + i * 0.05, "advisory", "{}")
    session.commit()

    bars_map = {f"SIG{i}": _bars([100, 100, 100, 100, 100, 101 + i, 101 + i, 101 + i, 101 + i])
               for i in range(5)}
    provider = FixedBarsProvider(bars_map=bars_map)

    result = build_forward_returns(session, provider, horizons=(1,), now_utc=FR_NOW_UTC)

    signal = result["by_horizon"]["1"]["confidence_signal"]
    assert signal["n"] == 5
    assert signal["pearson_r"] is None
    assert signal["verdict"] is None
    assert "样本不足" in signal["note"]
    assert "20" in signal["note"]


def _spread_over_days_bars(day_idx: int, ret: float) -> pd.DataFrame:
    """一条决策的行情:as_of 落在 FR_DATES[day_idx](收 100),下一根 bar 收
    100+ret —— 于是 1 日 horizon 的收益恰好等于 ret%,且决策可以散布在不同日子。"""
    closes = [100.0] * len(FR_DATES)
    closes[day_idx + 1] = 100.0 + ret
    return _bars(closes)


def test_forward_returns_confidence_signal_correlated_synthetic_buys(session):
    # perfectly linear confidence -> return relation: return_i = 2*i, confidence_i = 0.5+0.01*i.
    # entry fixed at 100, so exit = 100 + return_i reproduces return_i exactly.
    # Spread across MIN_SIGNAL_DAYS distinct decision days so the day gate passes
    # and this test keeps exercising the correlation math itself.
    assert MIN_SIGNAL_N == 20 and MIN_SIGNAL_DAYS == 5
    n = MIN_SIGNAL_N
    bars_map = {}
    for i in range(n):
        confidence = round(0.5 + 0.01 * i, 3)
        ret = 2 * i  # 0, 2, 4, ..., 38
        day_idx = i % MIN_SIGNAL_DAYS  # FR_DATES[0..4]
        save_decision(session, dt.date.fromisoformat(FR_DATES[day_idx]), f"SIG{i}",
                      "buy", confidence, "advisory", "{}")
        bars_map[f"SIG{i}"] = _spread_over_days_bars(day_idx, ret)
    session.commit()

    provider = FixedBarsProvider(bars_map=bars_map)
    result = build_forward_returns(session, provider, horizons=(1,), now_utc=FR_NOW_UTC)

    signal = result["by_horizon"]["1"]["confidence_signal"]
    assert signal["n"] == n
    assert signal["distinct_days"] == MIN_SIGNAL_DAYS
    # a perfectly linear confidence/return relationship has r == +1.0 exactly.
    assert signal["pearson_r"] == pytest.approx(1.0)
    assert signal["pearson_r"] > 0.5
    assert "正相关" in signal["verdict"]
    assert "note" not in signal


def test_forward_returns_confidence_signal_same_day_pile_refuses_conclusion(session):
    """条数够但全来自同一天 → 拒绝下结论。

    同一天的 N 只标的一起随大盘涨跌,是 1 个观测不是 N 个;只数条数的门控会把
    "那天大盘涨了"误读成"置信度有效"。这正是 2026-07-21 真实数据踩到的坑
    (36 条同天买入产出 r=0.112 并被当成结论输出)。
    """
    n = MIN_SIGNAL_N + 16  # 36 条,远超条数门槛
    bars_map = {}
    for i in range(n):
        confidence = round(0.5 + 0.01 * i, 3)
        save_decision(session, FR_AS_OF, f"SAMEDAY{i}", "buy", confidence, "advisory", "{}")
        bars_map[f"SAMEDAY{i}"] = _spread_over_days_bars(4, 2 * i)  # idx4 == FR_AS_OF
    session.commit()

    provider = FixedBarsProvider(bars_map=bars_map)
    result = build_forward_returns(session, provider, horizons=(1,), now_utc=FR_NOW_UTC)

    signal = result["by_horizon"]["1"]["confidence_signal"]
    assert signal["n"] == n  # 条数够
    assert signal["distinct_days"] == 1  # 但只有一天
    assert signal["pearson_r"] is None  # 即便相关性完美也不给数
    assert signal["verdict"] is None
    assert f"≥{MIN_SIGNAL_DAYS} 天" in signal["note"]


def test_forward_returns_confidence_signal_zero_variance_no_conclusion(session):
    # >= MIN_SIGNAL_N matured buys spanning enough days (so the day gate passes)
    # but every confidence is identical -> undefined correlation, must not be
    # fabricated as 0 or crash.
    n = MIN_SIGNAL_N + 5
    bars_map = {}
    for i in range(n):
        day_idx = i % MIN_SIGNAL_DAYS
        save_decision(session, dt.date.fromisoformat(FR_DATES[day_idx]), f"FLATSIG{i}",
                      "buy", 0.8, "advisory", "{}")
        bars_map[f"FLATSIG{i}"] = _spread_over_days_bars(day_idx, i)  # returns vary
    session.commit()

    provider = FixedBarsProvider(bars_map=bars_map)
    result = build_forward_returns(session, provider, horizons=(1,), now_utc=FR_NOW_UTC)

    signal = result["by_horizon"]["1"]["confidence_signal"]
    assert signal["n"] == n
    assert signal["distinct_days"] == MIN_SIGNAL_DAYS  # 天数门已过
    assert signal["pearson_r"] is None  # 卡在方差为 0
    assert signal["verdict"] is None
    assert "没有变化" in signal["note"]


def test_forward_returns_empty_db_zeros_and_note_no_crash(session):
    provider = FixedBarsProvider()

    result = build_forward_returns(session, provider, horizons=(1, 5, 20), now_utc=FR_NOW_UTC)

    assert result["total_decisions"] == 0
    assert result["distinct_symbols"] == 0
    assert result["as_of_from"] is None
    assert result["as_of_to"] is None
    assert result["horizons"] == [1, 5, 20]
    assert "暂无决策数据" in result["note"]

    for h_key in ("1", "5", "20"):
        block = result["by_horizon"][h_key]
        assert block["coverage"] == {"matured": 0, "pending": 0, "unpriced": 0}
        for action in ("buy", "sell", "hold"):
            stats = block["by_action"][action]
            assert stats["n"] == 0
            assert stats["mean_return_pct"] is None
            assert stats["median_return_pct"] is None
            assert stats["hit_rate"] is None
        assert all(b["n"] == 0 and b["mean_return_pct"] is None
                  for b in block["buy_by_confidence"])
        assert block["confidence_signal"] == {
            "n": 0, "distinct_days": 0, "pearson_r": None, "verdict": None,
            "note": f"样本不足(需≥{MIN_SIGNAL_N}条已成熟买入决策),暂不下结论",
        }


def test_forward_returns_days_window_filters_like_scorecard(session):
    save_decision(session, dt.date(2026, 6, 1), "OLD", "buy", 0.8, "advisory", "{}")
    save_decision(session, dt.date(2026, 7, 6), "RECENT", "buy", 0.8, "advisory", "{}")
    session.commit()

    provider = FixedBarsProvider(bars_map={
        "RECENT": _bars([100, 101, 102, 103, 104, 105, 106, 107, 108]),
    })

    # FR_NOW_UTC -> ET 2026-07-08; days=5 -> since = 2026-07-03. RECENT (07-06)
    # is in-window, OLD (06-01) is not.
    result = build_forward_returns(session, provider, horizons=(1,), days=5, now_utc=FR_NOW_UTC)

    assert result["total_decisions"] == 1
    assert result["as_of_from"] == "2026-07-06"
    assert result["as_of_to"] == "2026-07-06"

    result_all = build_forward_returns(session, provider, horizons=(1,), now_utc=FR_NOW_UTC)
    assert result_all["total_decisions"] == 2
