import logging
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.store.models import Base

logger = logging.getLogger(__name__)


def make_engine(db_path) -> Engine:
    """SQLite engine。db_path 为文件路径或 ":memory:"(测试用)。"""
    path = str(db_path)
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{path}")


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    _ensure_execution_backend_column(engine)


def _ensure_execution_backend_column(engine: Engine) -> None:
    """老 DB 补列守卫:create_all 不会给已存在的表加新列,已有本地 SQLite DB 的
    settings 表可能还没有 execution_backend。幂等检查 + ALTER,默认 'paper'
    (不改变既有行为)。只对 sqlite 生效;任何失败都吞掉,绝不阻塞启动。"""
    if engine.dialect.name != "sqlite":
        return
    try:
        with engine.connect() as conn:
            cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(settings)")}
            if cols and "execution_backend" not in cols:
                conn.exec_driver_sql(
                    "ALTER TABLE settings ADD COLUMN execution_backend VARCHAR(16) "
                    "DEFAULT 'paper'")
                conn.commit()
    except Exception:
        logger.warning("execution_backend 列迁移守卫失败,忽略", exc_info=True)


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)
