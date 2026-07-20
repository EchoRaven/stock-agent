"""tests/api 共享 fixture:内存 SQLite + FastAPI TestClient + 依赖覆盖注入。

全离线(见 tests/conftest.py 的联网熔断):FakeProvider 不发起任何网络请求,
session/provider 都通过 app.dependency_overrides 注入,不触碰真实 DB/行情源。
"""
import datetime as dt

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.api.deps import get_provider, get_session
from app.data.base import PriceProvider, empty_bars
from app.main import app
from app.store.db import init_db, make_session_factory


class FakeProvider(PriceProvider):
    """离线测试行情源:固定收盘价的整段日线,不发起任何网络请求。"""

    def __init__(self, price: float = 100.0):
        self._price = price

    def get_daily_bars(self, symbol: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        if start > end:
            return empty_bars()
        idx = pd.date_range(start, end, freq="D")
        return pd.DataFrame(
            {"open": self._price, "high": self._price, "low": self._price,
             "close": self._price, "volume": 1_000_000.0},
            index=idx,
        )


@pytest.fixture
def session():
    # TestClient 通过独立线程跑 ASGI app,普通 sqlite3 连接不能跨线程复用;
    # 用 StaticPool + check_same_thread=False 让同一个内存库连接可跨线程访问
    # (仅测试内构造,不改动 app/store/db.py 的生产 make_engine)。
    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_provider] = lambda: FakeProvider()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
