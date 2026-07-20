"""GET/POST /api/memory —— agent 知识库(Phase 1)。token 门禁的专门覆盖(403
without token / 200 with token)在 tests/api/test_security.py(unsecured_client
的既有模式),这里用共享的 client fixture(已 override require_token)覆盖
业务行为:列表/过滤/新增/幂等播种/非法 kind 400。"""


def test_get_memory_before_seed_is_empty(client):
    resp = client.get("/api/memory")
    assert resp.status_code == 200
    assert resp.json() == []  # 尚未播种,GET 本身纯只读,不触发播种


def test_post_seed_inserts_six_entries(client):
    resp = client.post("/api/memory/seed")
    assert resp.status_code == 200
    assert resp.json()["inserted"] == 6

    listed = client.get("/api/memory").json()
    assert len(listed) == 6
    assert any("元结论" in row["title"] for row in listed)
    assert all(row["source"] == "seed_experiment" for row in listed)


def test_post_seed_is_idempotent(client):
    first = client.post("/api/memory/seed").json()
    second = client.post("/api/memory/seed").json()
    assert first["inserted"] == 6
    assert second["inserted"] == 0
    assert len(client.get("/api/memory").json()) == 6


def test_post_memory_adds_manual_entry(client):
    resp = client.post("/api/memory", json={
        "kind": "trade_review", "title": "复盘: AAPL 卖飞",
        "body": "过早止盈,少赚 8%", "symbol": "AAPL",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "manual"
    assert body["symbol"] == "AAPL"
    assert body["kind"] == "trade_review"

    listed = client.get("/api/memory?symbol=AAPL").json()
    assert len(listed) == 1 and listed[0]["title"] == "复盘: AAPL 卖飞"


def test_post_memory_invalid_kind_returns_400(client):
    resp = client.post("/api/memory", json={
        "kind": "bogus_kind", "title": "t", "body": "b",
    })
    assert resp.status_code == 400


def test_post_memory_unknown_field_rejected(client):
    resp = client.post("/api/memory", json={
        "kind": "insight", "title": "t", "body": "b", "extra_field": 1,
    })
    assert resp.status_code == 422


def test_post_memory_empty_title_rejected(client):
    resp = client.post("/api/memory", json={"kind": "insight", "title": "", "body": "b"})
    assert resp.status_code == 422


def test_get_memory_filters_by_kind(client):
    client.post("/api/memory/seed")
    factors = client.get("/api/memory?kind=factor").json()
    assert len(factors) == 4
    assert all(row["kind"] == "factor" for row in factors)


def test_get_memory_limit(client):
    client.post("/api/memory/seed")
    limited = client.get("/api/memory?limit=2").json()
    assert len(limited) == 2
