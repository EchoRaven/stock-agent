import datetime as dt

import pytest

import app.mcp.runtime as runtime
from app.data.base import PriceProvider
from app.mcp.tool_decision import submit_decision
from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.decision_repo import get_decisions
from app.store.repos.settings_repo import MODE_SEMI_AUTO, set_mode
from tests.helpers import make_bars, make_decision_payload


@pytest.fixture
def factory(monkeypatch):
    engine = make_engine(":memory:")
    init_db(engine)
    factory = make_session_factory(engine)
    monkeypatch.setattr(runtime, "open_session", lambda: factory())
    return factory


def test_valid_payload_recorded(factory):
    result = submit_decision(make_decision_payload())
    assert result["status"] == "recorded" and result["mode"] == "advisory"
    with factory() as session:
        assert len(get_decisions(session, dt.date(2026, 7, 17))) == 1


def test_invalid_payload_rejected_not_raised(factory):
    result = submit_decision(make_decision_payload(confidence=2.0))
    assert result["status"] == "rejected"
    assert "confidence" in result["error"]
    with factory() as session:
        assert get_decisions(session, dt.date(2026, 7, 17)) == []


class AnchoredPrices(PriceProvider):
    def get_daily_bars(self, symbol, start, end):
        return make_bars(start=(end - dt.timedelta(days=13)).isoformat(), days=10, base=100.0)


def test_db_semi_auto_routes_via_tool(factory, monkeypatch):
    # mode 从 DB 读;闸门参考价由服务端 provider 取,payload 无价格通道
    monkeypatch.setattr(runtime, "get_price_provider", lambda: AnchoredPrices())
    with factory() as session:
        set_mode(session, MODE_SEMI_AUTO)
        session.commit()
    result = submit_decision(make_decision_payload())
    assert result["mode"] == "semi_auto"
    assert result["order"]["status"] == "pending_confirmation"
