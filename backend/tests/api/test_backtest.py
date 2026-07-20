def test_backtest_bad_dates_returns_400_offline(client):
    # start > end 在 BacktestConfig.__post_init__ 校验,不会触发任何取价——
    # 全离线可测(见 tests/conftest.py 的联网熔断)。
    resp = client.post("/api/backtest", json={
        "start": "2026-02-01", "end": "2026-01-01",
    })
    assert resp.status_code == 400


def test_backtest_with_fake_provider_returns_metrics_and_curve(client):
    resp = client.post("/api/backtest", json={
        "start": "2024-01-08", "end": "2024-01-12",
        "cash": 10_000, "max_positions": 1, "universe": ["AAPL"],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert "metrics" in body and "total_return" in body["metrics"]
    assert isinstance(body["equity_curve"], list) and len(body["equity_curve"]) > 0
    assert set(body["equity_curve"][0]) == {"date", "equity"}


def test_backtest_universe_too_large_returns_400(client):
    resp = client.post("/api/backtest", json={
        "start": "2024-01-08", "end": "2024-01-12",
        "universe": [f"SYM{i}" for i in range(51)],
    })
    assert resp.status_code == 400
