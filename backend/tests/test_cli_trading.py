import datetime as dt
import json

import pytest

import app.cli_trading as cli_trading
from app.cli import main
from app.data.base import PriceProvider
from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.order_repo import STATUS_PENDING_CONFIRMATION, create_order, get_order
from app.store.repos.settings_repo import MODE_SEMI_AUTO, get_mode, set_mode
from tests.helpers import make_bars

D = dt.date(2026, 7, 17)


class AnchoredProvider(PriceProvider):
    def get_daily_bars(self, symbol, start, end):
        return make_bars(start=(end - dt.timedelta(days=13)).isoformat(), days=10, base=100.0)


@pytest.fixture
def factory(monkeypatch):
    engine = make_engine(":memory:")
    init_db(engine)
    factory = make_session_factory(engine)
    monkeypatch.setattr(cli_trading, "open_cli_session", lambda: factory())
    monkeypatch.setattr(cli_trading, "_default_provider", lambda: AnchoredProvider())
    return factory


def test_mode_show_and_set(factory, capsys):
    assert main(["mode", "show"]) == 0
    assert "advisory" in capsys.readouterr().out
    assert main(["mode", "set", "semi_auto"]) == 0
    with factory() as session:
        assert get_mode(session) == "semi_auto"


def test_full_auto_requires_explicit_confirm(factory, capsys):
    # 红线:全自动开启需显式
    assert main(["mode", "set", "full_auto"]) == 2
    assert "confirm" in capsys.readouterr().out
    with factory() as session:
        assert get_mode(session) == "advisory"
    assert main(["mode", "set", "full_auto", "--confirm-full-auto"]) == 0
    with factory() as session:
        assert get_mode(session) == "full_auto"


def test_orders_list_empty(factory, capsys):
    assert main(["orders", "list"]) == 0
    assert "no pending orders" in capsys.readouterr().out


def test_orders_approve_and_settle(factory, capsys):
    with factory() as session:
        row = create_order(session, D, "AAPL", "buy", 5,
                           STATUS_PENDING_CONFIRMATION, "semi_auto")
        session.commit()
        order_id = row.id
    assert main(["orders", "approve", str(order_id)]) == 0
    with factory() as session:
        assert get_order(session, order_id).status == "submitted"
    assert main(["orders", "settle"]) == 0
    assert "1 fill(s)" in capsys.readouterr().out
    with factory() as session:
        assert get_order(session, order_id).status == "filled"


def test_orders_reject(factory, capsys):
    with factory() as session:
        row = create_order(session, D, "AAPL", "buy", 5,
                           STATUS_PENDING_CONFIRMATION, "semi_auto")
        session.commit()
        order_id = row.id
    assert main(["orders", "reject", str(order_id)]) == 0
    with factory() as session:
        assert get_order(session, order_id).status == "rejected"


def test_orders_approve_requires_id(factory, capsys):
    assert main(["orders", "approve"]) == 2


def test_orders_approve_nonexistent_id_returns_nonzero(factory, capsys):
    # 可脚本化:目标 order 不存在/非 pending → 无状态变化,退出码非 0
    assert main(["orders", "approve", "999999"]) == 2
    assert "is not pending confirmation" in capsys.readouterr().out


def test_orders_reject_nonexistent_id_returns_nonzero(factory, capsys):
    assert main(["orders", "reject", "999999"]) == 2
    assert "is not pending confirmation" in capsys.readouterr().out


def test_orders_approve_already_settled_returns_nonzero(factory, capsys):
    # 目标存在但已不是 pending_confirmation(已 approve 过)→ 二次 approve 是 no-op
    with factory() as session:
        row = create_order(session, D, "AAPL", "buy", 5,
                           STATUS_PENDING_CONFIRMATION, "semi_auto")
        session.commit()
        order_id = row.id
    assert main(["orders", "approve", str(order_id)]) == 0  # 真实状态迁移
    assert main(["orders", "approve", str(order_id)]) == 2  # 二次 no-op


def test_watchdog_reports_and_downgrades(factory, capsys):
    with factory() as session:
        set_mode(session, MODE_SEMI_AUTO)
        session.commit()
    assert main(["watchdog"]) == 1  # 无心跳 → unhealthy → 退出码 1
    out = json.loads(capsys.readouterr().out)
    assert out["downgraded"] is True and out["mode_after"] == "advisory"
