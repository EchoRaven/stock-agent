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
