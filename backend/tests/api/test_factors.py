"""POST /api/factors/mine —— Phase 4 自主因子挖掘的 token 门禁 + 业务薄壳。
编排逻辑本身(目录校验/双窗口回测门禁/写库)已在 tests/factors/test_miner.py
覆盖,这里只测装配(session/provider/gemini 注入)+ token 门禁 + 500 清理,
同 tests/api/test_reflect.py 的 unsecured_client 模式。窗口通过 monkeypatch
换成几周短窗,配合离线 FakeProvider,保持 offline & fast。
"""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.api import security
from app.api.deps import get_gemini_client, get_provider, get_session
from app.api.security import current_token, require_token
from app.factors import miner
from app.main import app
from app.store.repos.memory_repo import get_entries
from tests.api.conftest import FakeProvider

_SHORT_WINDOWS = [
    ("tiny_a", dt.date(2024, 1, 2), dt.date(2024, 1, 19)),
    ("tiny_b", dt.date(2024, 2, 1), dt.date(2024, 2, 16)),
]


@pytest.fixture(autouse=True)
def _short_windows(monkeypatch):
    monkeypatch.setattr(miner, "MINING_WINDOWS", _SHORT_WINDOWS)


class _FakeGeminiMomentum:
    def generate_json(self, prompt):
        return {"proposals": [
            {"factor": "momentum", "params": {"window": 60}, "hypothesis": "test"},
        ]}


def test_post_factors_mine_returns_results_and_writes_memory(client, session):
    app.dependency_overrides[get_gemini_client] = lambda: _FakeGeminiMomentum()
    try:
        resp = client.post("/api/factors/mine", json={"n": 1})
    finally:
        app.dependency_overrides.pop(get_gemini_client, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert len(body["results"]) == 1
    assert body["results"][0]["factor"] == "momentum"
    assert body["results"][0]["verdict"] in ("validated", "no_improvement", "refuted")

    rows = [r for r in get_entries(session, kind="factor") if r.source == "agent"]
    assert len(rows) == 1


def test_post_factors_mine_defaults_n_to_three_when_body_omitted(client):
    # 显式把 gemini 覆盖成 None——不依赖 .env 里是否配置了真实 key(同其它
    # tests/api/test_*.py 的一贯做法),回退到种子提案(<=3 条),全离线。
    app.dependency_overrides[get_gemini_client] = lambda: None
    try:
        resp = client.post("/api/factors/mine")
    finally:
        app.dependency_overrides.pop(get_gemini_client, None)
    assert resp.status_code == 200
    assert resp.json()["count"] <= 3


def test_post_factors_mine_rejects_n_out_of_bounds(client):
    resp = client.post("/api/factors/mine", json={"n": 0})
    assert resp.status_code == 422
    resp = client.post("/api/factors/mine", json={"n": 6})
    assert resp.status_code == 422


def test_post_factors_mine_gemini_none_uses_seed_proposals(client):
    app.dependency_overrides[get_gemini_client] = lambda: None
    try:
        resp = client.post("/api/factors/mine", json={"n": 1})
    finally:
        app.dependency_overrides.pop(get_gemini_client, None)
    assert resp.status_code == 200
    assert resp.json()["count"] == 1


# ---------------------------------------------------------------------------
# token 门禁(状态变更路由,同 /api/trade/cycle、/api/reflect)。
# ---------------------------------------------------------------------------


@pytest.fixture
def token_env(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKAGENT_DB_PATH", str(tmp_path / "stockagent.db"))
    security._TOKEN_CACHE.clear()
    yield
    security._TOKEN_CACHE.clear()


@pytest.fixture
def unsecured_client(session, token_env):
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_provider] = lambda: FakeProvider()
    app.dependency_overrides[get_gemini_client] = lambda: None
    app.dependency_overrides.pop(require_token, None)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_post_factors_mine_without_token_is_forbidden(unsecured_client, session):
    resp = unsecured_client.post("/api/factors/mine", json={"n": 1})
    assert resp.status_code == 403
    assert get_entries(session, kind="factor") == []  # 无副作用


def test_post_factors_mine_with_correct_token_succeeds(unsecured_client, token_env):
    token = current_token()
    resp = unsecured_client.post("/api/factors/mine", json={"n": 1},
                                 headers={"X-Stock-Agent-Token": token})
    assert resp.status_code == 200
    assert resp.json()["count"] == 1
