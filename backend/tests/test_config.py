from pathlib import Path

from app.config import Settings, get_settings


def test_defaults():
    s = Settings()
    assert s.top_n == 10
    assert s.lookback_days == 400
    assert s.cache_dir == Path("data_cache")
    assert s.reports_dir == Path("reports")


def test_env_override(monkeypatch):
    monkeypatch.setenv("STOCKAGENT_TOP_N", "5")
    assert Settings().top_n == 5


def test_get_settings_returns_settings():
    assert isinstance(get_settings(), Settings)


def test_m2_defaults():
    s = Settings()
    assert s.db_path == Path("stockagent.db")
    assert s.finnhub_api_key == ""
    assert "stock-agent" in s.edgar_user_agent


def test_m2_env_override(monkeypatch):
    monkeypatch.setenv("STOCKAGENT_DB_PATH", "/tmp/x.db")
    monkeypatch.setenv("STOCKAGENT_FINNHUB_API_KEY", "k123")
    s = Settings()
    assert s.db_path == Path("/tmp/x.db")
    assert s.finnhub_api_key == "k123"
