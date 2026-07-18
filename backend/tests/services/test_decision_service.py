import datetime as dt
import json

import pytest

from app.services.decision_service import (ACTIONS, DecisionValidationError,
                                           submit_decision, validate_decision)
from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.decision_repo import get_decisions
from tests.helpers import make_decision_payload


def test_validate_normalizes():
    out = validate_decision(make_decision_payload(symbol="aapl "))
    assert out["symbol"] == "AAPL"
    assert out["mode"] == "advisory"
    assert out["confidence"] == 0.8


def test_mode_cannot_be_forced_by_caller():
    out = validate_decision(make_decision_payload(mode="auto"))
    assert out["mode"] == "advisory"  # 服务端强制,不信任调用方


@pytest.mark.parametrize("bad", [
    {"action": "yolo"},
    {"confidence": 1.5},
    {"confidence": "high"},
    {"confidence": True},
    {"symbol": "  "},
    {"as_of": "not-a-date"},
    {"chair": {"verdict": "买入", "bear_rebuttal": ""}},
])
def test_validate_rejects(bad):
    with pytest.raises(DecisionValidationError):
        validate_decision(make_decision_payload(**bad))


def test_validate_requires_all_roles():
    payload = make_decision_payload()
    del payload["committee"]["bear"]
    with pytest.raises(DecisionValidationError):
        validate_decision(payload)


def test_actions_constant():
    assert ACTIONS == ("buy", "sell", "hold")


def test_submit_persists():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as session:
        result = submit_decision(session, make_decision_payload())
        assert result["status"] == "recorded" and result["id"] is not None
        assert result["mode"] == "advisory"
        rows = get_decisions(session, dt.date(2026, 7, 17))
        assert len(rows) == 1 and rows[0].mode == "advisory"
        assert json.loads(rows[0].payload_json)["chair"]["bear_rebuttal"]
