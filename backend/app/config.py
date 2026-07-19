from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置,环境变量前缀 STOCKAGENT_(如 STOCKAGENT_TOP_N=5)。"""

    model_config = SettingsConfigDict(env_prefix="STOCKAGENT_", env_file=".env")

    cache_dir: Path = Path("data_cache")
    reports_dir: Path = Path("reports")
    top_n: int = 10
    lookback_days: int = 400

    # M2 新增
    db_path: Path = Path("stockagent.db")
    finnhub_api_key: str = ""  # 可空:无 key 时新闻返回空并告警,不崩
    edgar_user_agent: str = "stock-agent/0.1 (set STOCKAGENT_EDGAR_USER_AGENT)"

    # M3 新增(LLM)
    gemini_api_key: str = ""  # 可空:无 key 时 LLM 调用返回 None 并告警,不崩;从不硬编码
    gemini_model: str = "gemini-2.5-flash"


def get_settings() -> Settings:
    return Settings()
