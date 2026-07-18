import datetime as dt

from app.report.daily import render_daily_report
from app.screener.base import RuleResult, SymbolScore
from app.services.decision_service import submit_decision
from app.services.report_service import build_daily_report, generate_daily_report
from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.report_repo import get_report
from app.store.repos.signal_repo import save_signals
from tests.helpers import make_decision_payload

D = dt.date(2026, 7, 17)


def _session():
    engine = make_engine(":memory:")
    init_db(engine)
    return make_session_factory(engine)()


def test_render_daily_report_empty_sections():
    text = render_daily_report(D, [], [])
    assert "2026-07-17" in text
    assert "无筛选快照" in text and "无决定" in text


def test_generate_daily_report_persists_and_writes(tmp_path):
    with _session() as session:
        save_signals(session, D, [SymbolScore("AAPL", 0.9, {"trend": RuleResult(1.0, "up")})])
        submit_decision(session, make_decision_payload())
        text, path = generate_daily_report(session, D, tmp_path)
        assert "AAPL" in text and "buy" in text and "小仓位买入" in text
        assert path == tmp_path / "daily_20260717.md"
        assert path.read_text() == text
        assert get_report(session, D).content_md == text
        # 同日重跑:覆盖而非报错
        text2, _ = generate_daily_report(session, D, tmp_path)
        assert get_report(session, D).content_md == text2
        assert build_daily_report(session, D) == text2
