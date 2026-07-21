"""app.services.scorecard_service —— 决策记分卡:委员会推荐是否有区分度的纯
聚合测量。全离线(in-memory SQLite,不发起任何网络请求),直接调
build_scorecard(不经 HTTP 层);路由层行为在 tests/api/test_history.py 里覆盖。
"""
import datetime as dt
import statistics

import pytest

from app.services.scorecard_service import build_scorecard
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
