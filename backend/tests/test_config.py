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


def test_gemini_defaults_isolated(tmp_path, monkeypatch):
    """隔离目录(无 .env、无环境变量)下的默认值,避免读到仓库里 backend/.env 的真实 key。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STOCKAGENT_GEMINI_API_KEY", raising=False)
    s = Settings()
    assert s.gemini_api_key == ""
    assert s.gemini_model == "gemini-2.5-flash"


def test_gemini_env_override(monkeypatch):
    monkeypatch.setenv("STOCKAGENT_GEMINI_API_KEY", "fake-test-key-not-real")
    monkeypatch.setenv("STOCKAGENT_GEMINI_MODEL", "gemini-x-test")
    s = Settings()
    assert s.gemini_api_key == "fake-test-key-not-real"
    assert s.gemini_model == "gemini-x-test"


def test_settings_reads_dotenv_file(tmp_path, monkeypatch):
    """验证 model_config 配置了 env_file 读取 .env;用临时目录里的假 key,绝不碰真实 backend/.env。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STOCKAGENT_GEMINI_API_KEY", raising=False)
    (tmp_path / ".env").write_text("STOCKAGENT_GEMINI_API_KEY=dotenv-fake-key\n")
    s = Settings()
    assert s.gemini_api_key == "dotenv-fake-key"
