"""POST /api/watchdog —— 触发一次心跳检查(镜像 cli_trading.cmd_watchdog)。
token 门禁另见 tests/api/test_security.py。"""
from app.store.repos.settings_repo import MODE_FULL_AUTO, get_mode, set_mode


def test_watchdog_already_advisory_no_heartbeat_stays_advisory_undowngraded(client, session):
    # mode 已是 advisory(默认)时,即使无心跳(不健康),没有更低的模式可降级
    # 到——check_and_enforce 只在 mode_before != advisory 时才触发降级动作。
    resp = client.post("/api/watchdog")
    assert resp.status_code == 200
    body = resp.json()
    assert "healthy" in body and "downgraded" in body and "reasons" in body
    assert body["healthy"] is False
    assert body["downgraded"] is False
    assert body["mode_after"] == "advisory"


def test_watchdog_downgrades_full_auto_with_no_heartbeat(client, session):
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    session.commit()
    resp = client.post("/api/watchdog")
    assert resp.status_code == 200
    body = resp.json()
    assert body["downgraded"] is True
    assert body["mode_after"] == "advisory"
    assert get_mode(session) == "advisory"
