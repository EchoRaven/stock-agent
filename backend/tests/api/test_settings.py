"""安全红线覆盖:full_auto 需显式 confirm;settings 响应绝不含密钥。"""


def test_settings_default_advisory(client):
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "advisory"
    assert body["single_position_cap_pct"] == 0.20


def test_settings_response_has_no_secrets(client):
    resp = client.get("/api/settings")
    body = resp.json()
    lowered = str(body).lower()
    assert "gemini" not in lowered
    assert "finnhub" not in lowered
    assert all("key" not in k.lower() for k in body)


def test_mode_full_auto_without_confirm_is_refused(client):
    resp = client.post("/api/settings/mode", json={"mode": "full_auto"})
    assert resp.status_code == 400
    assert client.get("/api/settings").json()["mode"] == "advisory"


def test_mode_full_auto_with_confirm_succeeds(client):
    resp = client.post("/api/settings/mode",
                       json={"mode": "full_auto", "confirm_full_auto": True})
    assert resp.status_code == 200
    assert resp.json()["mode"] == "full_auto"


def test_mode_semi_auto_switch_ok_without_confirm(client):
    resp = client.post("/api/settings/mode", json={"mode": "semi_auto"})
    assert resp.status_code == 200
    assert resp.json()["mode"] == "semi_auto"


def test_mode_invalid_value_returns_400(client):
    resp = client.post("/api/settings/mode", json={"mode": "bogus"})
    assert resp.status_code == 400


def test_risk_params_partial_update_persists(client):
    resp = client.post("/api/settings/risk", json={"cooldown_days": 9})
    assert resp.status_code == 200
    body = resp.json()
    assert body["cooldown_days"] == 9
    assert body["single_position_cap_pct"] == 0.20  # 未提交字段不变


def test_risk_params_unknown_field_rejected(client):
    resp = client.post("/api/settings/risk", json={"bogus_field": 1})
    assert resp.status_code == 422
