from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置,环境变量前缀 STOCKAGENT_(如 STOCKAGENT_TOP_N=5)。"""

    model_config = SettingsConfigDict(env_prefix="STOCKAGENT_")

    cache_dir: Path = Path("data_cache")
    reports_dir: Path = Path("reports")
    top_n: int = 10
    lookback_days: int = 400


def get_settings() -> Settings:
    return Settings()
