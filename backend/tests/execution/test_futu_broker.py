"""FutuBroker 离线测试(mock futu SDK)——真实网关不可达,这里只验证安全门与对账逻辑。

覆盖 5 条硬性属性:
(a) 默认模拟盘(SIMULATE),从不调用 unlock_trade;
(b) REAL 必须同时 allow_real=True 且 unlock_pwd 非空,否则拒绝,且不下单;
(c) FutuBroker 公开方法只有 submit/process_fills(与 no_fund_egress 红线一致);
(d) 解锁密码不出现在日志或异常信息里;
(e) 不装 futu 时本模块也能正常 import。
"""
import datetime as dt
import logging
import sys
import types

import pandas as pd
import pytest

from app.config import Settings
from app.execution.base import Broker
from app.execution.futu_broker import FutuBroker
from app.store.db import init_db, make_engine, make_session_factory
from app.store.models import OrderRow, PaperAccountRow
from app.store.repos.order_repo import (STATUS_APPROVED, STATUS_CANCELLED,
                                        STATUS_FILLED, STATUS_SUBMITTED,
                                        create_order, get_order)
from app.store.repos.paper_repo import get_account, get_fills, get_position, set_position

D = dt.date(2026, 7, 17)
D1 = dt.date(2026, 7, 20)


# ---------------------------------------------------------------------------
# 假 futu SDK:只提供 FutuBroker 用到的名字,记录调用,返回可控 (ret, data)。
# ---------------------------------------------------------------------------

class FakeCtx:
    def __init__(self, unlock_ret=0, place_order_ret=0, deal_rows=None):
        self.calls = []
        self.closed = False
        self._unlock_ret = unlock_ret
        self._place_order_ret = place_order_ret
        self._deal_rows = deal_rows if deal_rows is not None else []

    def unlock_trade(self, password=None):
        self.calls.append(("unlock_trade", password))
        if self._unlock_ret != 0:
            return self._unlock_ret, "unlock failed: invalid password"
        return 0, pd.DataFrame()

    def place_order(self, price=None, qty=None, code=None, trd_side=None,
                    order_type=None, trd_env=None):
        self.calls.append(("place_order", {"price": price, "qty": qty, "code": code,
                                            "trd_side": trd_side, "order_type": order_type,
                                            "trd_env": trd_env}))
        if self._place_order_ret != 0:
            return self._place_order_ret, "order rejected by broker"
        return 0, pd.DataFrame({"order_id": ["ORD-1"]})

    def deal_list_query(self, trd_env=None):
        self.calls.append(("deal_list_query", trd_env))
        return 0, pd.DataFrame(self._deal_rows)

    def close(self):
        self.calls.append(("close", None))
        self.closed = True


def make_fake_futu_module(unlock_ret=0, place_order_ret=0, deal_rows=None, created=None):
    """构造一个假 `futu` 模块,并把每次创建的 FakeCtx 追加进 created 列表。"""
    created = created if created is not None else []
    mod = types.ModuleType("futu")

    class TrdEnv:
        SIMULATE = "SIMULATE_ENV"
        REAL = "REAL_ENV"

    class TrdSide:
        BUY = "BUY_SIDE"
        SELL = "SELL_SIDE"

    class OrderType:
        MARKET = "MARKET_TYPE"
        NORMAL = "NORMAL_TYPE"

    class TrdMarket:
        US = "US_MARKET"

    def OpenSecTradeContext(filter_trdmarket=None, host=None, port=None):
        ctx = FakeCtx(unlock_ret=unlock_ret, place_order_ret=place_order_ret,
                      deal_rows=deal_rows)
        ctx.filter_trdmarket = filter_trdmarket
        ctx.host = host
        ctx.port = port
        created.append(ctx)
        return ctx

    mod.TrdEnv = TrdEnv
    mod.TrdSide = TrdSide
    mod.OrderType = OrderType
    mod.TrdMarket = TrdMarket
    mod.OpenSecTradeContext = OpenSecTradeContext
    mod.RET_OK = 0
    return mod, created


@pytest.fixture
def engine():
    engine = make_engine(":memory:")
    init_db(engine)
    return engine


@pytest.fixture
def session(engine):
    with make_session_factory(engine)() as s:
        yield s


def _settings(**overrides):
    base = dict(futu_host="127.0.0.1", futu_port=11111, futu_trd_env="SIMULATE",
               futu_market="US", futu_unlock_pwd="", futu_allow_real=False)
    base.update(overrides)
    return Settings(**base)


def _approved(session, symbol="AAPL", side="buy", shares=10):
    return create_order(session, D, symbol, side, shares, STATUS_APPROVED, "full_auto")


# ---------------------------------------------------------------------------
# (e) 不装 futu 也能 import
# ---------------------------------------------------------------------------

def test_module_imports_without_futu_installed():
    import app.execution.futu_broker as futu_broker_module
    assert issubclass(futu_broker_module.FutuBroker, Broker)


# ---------------------------------------------------------------------------
# (c) 只暴露 submit/process_fills
# ---------------------------------------------------------------------------

def test_only_public_methods_are_submit_and_process_fills():
    public = {n for n in vars(FutuBroker) if not n.startswith("_")
             and callable(getattr(FutuBroker, n))}
    assert public == {"submit", "process_fills"}


# ---------------------------------------------------------------------------
# (a) 默认模拟盘
# ---------------------------------------------------------------------------

def test_defaults_to_simulate_and_never_unlocks(session, monkeypatch):
    fake_mod, created = make_fake_futu_module()
    monkeypatch.setitem(sys.modules, "futu", fake_mod)

    row = _approved(session)
    out = FutuBroker(_settings()).submit(session, row)

    assert out.status == STATUS_SUBMITTED
    assert "futu:SIMULATE:" in out.reason
    assert len(created) == 1
    ctx = created[0]
    call_names = [name for name, _ in ctx.calls]
    assert "unlock_trade" not in call_names
    _, place_kwargs = next(c for c in ctx.calls if c[0] == "place_order")
    assert place_kwargs["trd_env"] == fake_mod.TrdEnv.SIMULATE
    assert place_kwargs["code"] == "US.AAPL"
    assert place_kwargs["trd_side"] == fake_mod.TrdSide.BUY
    assert ("close", None) in ctx.calls


# ---------------------------------------------------------------------------
# (b) REAL 硬门
# ---------------------------------------------------------------------------

def test_real_refused_without_allow_real(session, monkeypatch):
    fake_mod, created = make_fake_futu_module()
    monkeypatch.setitem(sys.modules, "futu", fake_mod)

    row = _approved(session)
    broker = FutuBroker(_settings(futu_trd_env="REAL", futu_allow_real=False,
                                  futu_unlock_pwd=""))
    with pytest.raises(RuntimeError):
        broker.submit(session, row)

    assert get_order(session, row.id).status == STATUS_APPROVED
    assert created == []


def test_real_refused_without_unlock_pwd(session, monkeypatch):
    fake_mod, created = make_fake_futu_module()
    monkeypatch.setitem(sys.modules, "futu", fake_mod)

    row = _approved(session)
    broker = FutuBroker(_settings(futu_trd_env="REAL", futu_allow_real=True,
                                  futu_unlock_pwd=""))
    with pytest.raises(RuntimeError):
        broker.submit(session, row)

    assert get_order(session, row.id).status == STATUS_APPROVED
    assert created == []


def test_real_enabled_calls_unlock_then_place_order(session, monkeypatch):
    fake_mod, created = make_fake_futu_module()
    monkeypatch.setitem(sys.modules, "futu", fake_mod)

    row = _approved(session)
    broker = FutuBroker(_settings(futu_trd_env="REAL", futu_allow_real=True,
                                  futu_unlock_pwd="secret-pwd"))
    out = broker.submit(session, row)

    assert out.status == STATUS_SUBMITTED
    assert "futu:REAL:" in out.reason
    assert len(created) == 1
    ctx = created[0]
    call_names = [name for name, _ in ctx.calls]
    assert call_names.index("unlock_trade") < call_names.index("place_order")
    assert ("unlock_trade", "secret-pwd") in ctx.calls
    _, place_kwargs = next(c for c in ctx.calls if c[0] == "place_order")
    assert place_kwargs["trd_env"] == fake_mod.TrdEnv.REAL


def test_invalid_trd_env_raises_value_error(session, monkeypatch):
    fake_mod, created = make_fake_futu_module()
    monkeypatch.setitem(sys.modules, "futu", fake_mod)

    row = _approved(session)
    broker = FutuBroker(_settings(futu_trd_env="BOGUS"))
    with pytest.raises(ValueError):
        broker.submit(session, row)
    assert created == []


# ---------------------------------------------------------------------------
# (d) 密码永不出现在日志/异常信息里
# ---------------------------------------------------------------------------

def test_unlock_password_never_logged(session, monkeypatch, caplog):
    fake_mod, created = make_fake_futu_module()
    monkeypatch.setitem(sys.modules, "futu", fake_mod)
    caplog.set_level(logging.DEBUG)

    row = _approved(session)
    broker = FutuBroker(_settings(futu_trd_env="REAL", futu_allow_real=True,
                                  futu_unlock_pwd="super-secret-pwd"))
    broker.submit(session, row)

    assert "super-secret-pwd" not in caplog.text


def test_unlock_failure_error_does_not_leak_password_and_no_order_placed(session, monkeypatch):
    fake_mod, created = make_fake_futu_module(unlock_ret=1)
    monkeypatch.setitem(sys.modules, "futu", fake_mod)

    row = _approved(session)
    broker = FutuBroker(_settings(futu_trd_env="REAL", futu_allow_real=True,
                                  futu_unlock_pwd="topsecret-pwd"))
    with pytest.raises(RuntimeError) as exc_info:
        broker.submit(session, row)

    assert "topsecret-pwd" not in str(exc_info.value)
    ctx = created[0]
    call_names = [name for name, _ in ctx.calls]
    assert "place_order" not in call_names  # 解锁失败,绝不下单


# ---------------------------------------------------------------------------
# side/shares 校验(镜像 PaperBroker)
# ---------------------------------------------------------------------------

def test_invalid_side_raises_value_error(session, monkeypatch):
    fake_mod, created = make_fake_futu_module()
    monkeypatch.setitem(sys.modules, "futu", fake_mod)
    from app.store.models import OrderRow

    bad = OrderRow(as_of=D, symbol="X", side="hold", shares=1,
                   status=STATUS_APPROVED, mode="full_auto")
    with pytest.raises(ValueError):
        FutuBroker(_settings()).submit(session, bad)
    assert created == []


def test_non_positive_shares_raises_value_error(session, monkeypatch):
    fake_mod, created = make_fake_futu_module()
    monkeypatch.setitem(sys.modules, "futu", fake_mod)
    from app.store.models import OrderRow

    bad = OrderRow(as_of=D, symbol="X", side="buy", shares=0,
                   status=STATUS_APPROVED, mode="full_auto")
    with pytest.raises(ValueError):
        FutuBroker(_settings()).submit(session, bad)
    assert created == []


# ---------------------------------------------------------------------------
# place_order 被券商拒绝 -> cancelled + reason,不抛出
# ---------------------------------------------------------------------------

def test_place_order_rejected_marks_cancelled_not_raised(session, monkeypatch):
    fake_mod, created = make_fake_futu_module(place_order_ret=1)
    monkeypatch.setitem(sys.modules, "futu", fake_mod)

    row = _approved(session)
    out = FutuBroker(_settings()).submit(session, row)

    assert out.status == STATUS_CANCELLED
    assert "futu" in out.reason.lower()


# ---------------------------------------------------------------------------
# process_fills:对账券商成交
# ---------------------------------------------------------------------------

def test_process_fills_reconciles_broker_deal_into_fill(session, monkeypatch):
    deal_rows = [{"code": "US.AAPL", "qty": 10, "price": 151.23, "order_id": "ORD-1"}]
    fake_mod, created = make_fake_futu_module(deal_rows=deal_rows)
    monkeypatch.setitem(sys.modules, "futu", fake_mod)

    broker = FutuBroker(_settings())
    row = _approved(session, symbol="AAPL", side="buy", shares=10)
    broker.submit(session, row)

    fills = broker.process_fills(session, D1, {"AAPL": 999.0})  # open_prices 被忽略

    assert len(fills) == 1
    assert fills[0].shares == 10
    assert fills[0].price == pytest.approx(151.23)
    assert get_order(session, row.id).status == STATUS_FILLED
    assert len(get_fills(session)) == 1


def test_process_fills_leaves_unmatched_orders_submitted(session, monkeypatch):
    fake_mod, created = make_fake_futu_module(deal_rows=[])
    monkeypatch.setitem(sys.modules, "futu", fake_mod)

    broker = FutuBroker(_settings())
    row = _approved(session, symbol="AAPL", side="buy", shares=10)
    broker.submit(session, row)

    fills = broker.process_fills(session, D1, {})

    assert fills == []
    assert get_order(session, row.id).status == STATUS_SUBMITTED


def test_process_fills_guards_real_env_too(session, monkeypatch):
    fake_mod, created = make_fake_futu_module()
    monkeypatch.setitem(sys.modules, "futu", fake_mod)

    broker = FutuBroker(_settings(futu_trd_env="REAL", futu_allow_real=False))
    with pytest.raises(RuntimeError):
        broker.process_fills(session, D1, {})
    assert created == []


# ---------------------------------------------------------------------------
# C1: reconciled fills must mirror into the local cash/position ledger
# (RiskGate reads paper_repo.get_account/get_positions via build_account_state)
# ---------------------------------------------------------------------------

def test_reconciled_buy_updates_cash_and_position(session, monkeypatch):
    session.add(PaperAccountRow(id=1, cash=10_000.0))
    session.flush()

    deal_rows = [{"code": "US.AAPL", "qty": 10, "price": 151.23, "order_id": "ORD-1"}]
    fake_mod, created = make_fake_futu_module(deal_rows=deal_rows)
    monkeypatch.setitem(sys.modules, "futu", fake_mod)

    broker = FutuBroker(_settings())
    row = _approved(session, symbol="AAPL", side="buy", shares=10)
    broker.submit(session, row)  # FakeCtx.place_order always returns order_id "ORD-1"

    fills = broker.process_fills(session, D1, {"AAPL": 999.0})

    assert len(fills) == 1
    account = get_account(session, 10_000.0)
    assert account.cash == pytest.approx(10_000.0 - 10 * 151.23)
    position = get_position(session, "AAPL")
    assert position is not None
    assert position.shares == 10
    assert position.avg_cost == pytest.approx(151.23)


def test_reconciled_sell_updates_cash_and_position(session, monkeypatch):
    session.add(PaperAccountRow(id=1, cash=5_000.0))
    session.flush()
    set_position(session, "AAPL", 20, 100.0)

    deal_rows = [{"code": "US.AAPL", "qty": 10, "price": 160.0, "order_id": "ORD-1"}]
    fake_mod, created = make_fake_futu_module(deal_rows=deal_rows)
    monkeypatch.setitem(sys.modules, "futu", fake_mod)

    broker = FutuBroker(_settings())
    row = _approved(session, symbol="AAPL", side="sell", shares=10)
    broker.submit(session, row)

    fills = broker.process_fills(session, D1, {"AAPL": 999.0})

    assert len(fills) == 1
    account = get_account(session, 5_000.0)
    assert account.cash == pytest.approx(5_000.0 + 10 * 160.0)
    position = get_position(session, "AAPL")
    assert position is not None
    assert position.shares == 10
    assert position.avg_cost == pytest.approx(100.0)  # unchanged on sell


# ---------------------------------------------------------------------------
# I1: match deals to the specific order by broker order_id, not symbol
# ---------------------------------------------------------------------------

def test_deal_matched_by_order_id_not_symbol(session, monkeypatch):
    order_a = OrderRow(as_of=D, symbol="AAPL", side="buy", shares=5,
                       status=STATUS_SUBMITTED, mode="full_auto",
                       reason="futu:SIMULATE:ORD-A")
    order_b = OrderRow(as_of=D, symbol="AAPL", side="buy", shares=7,
                       status=STATUS_SUBMITTED, mode="full_auto",
                       reason="futu:SIMULATE:ORD-B")
    session.add_all([order_a, order_b])
    session.flush()

    # Only a deal for ORD-A is reported; same symbol, no deal at all for ORD-B.
    deal_rows = [{"code": "US.AAPL", "qty": 5, "price": 150.0, "order_id": "ORD-A"}]
    fake_mod, created = make_fake_futu_module(deal_rows=deal_rows)
    monkeypatch.setitem(sys.modules, "futu", fake_mod)

    broker = FutuBroker(_settings())
    fills = broker.process_fills(session, D1, {})

    assert len(fills) == 1
    assert fills[0].order_id == order_a.id
    assert fills[0].shares == 5
    assert fills[0].price == pytest.approx(150.0)
    assert get_order(session, order_a.id).status == STATUS_FILLED
    assert get_order(session, order_b.id).status == STATUS_SUBMITTED  # not force-filled


# ---------------------------------------------------------------------------
# M2: one malformed deal must not abort the whole reconciliation batch
# ---------------------------------------------------------------------------

def test_malformed_deal_does_not_abort_batch(session, monkeypatch):
    order_a = OrderRow(as_of=D, symbol="AAPL", side="buy", shares=5,
                       status=STATUS_SUBMITTED, mode="full_auto",
                       reason="futu:SIMULATE:ORD-A")
    order_b = OrderRow(as_of=D, symbol="MSFT", side="buy", shares=3,
                       status=STATUS_SUBMITTED, mode="full_auto",
                       reason="futu:SIMULATE:ORD-B")
    session.add_all([order_a, order_b])
    session.flush()

    # ORD-A's deal is missing "qty" -> becomes NaN once packed into a DataFrame
    # alongside ORD-B's well-formed row -> int(nan) raises inside reconciliation.
    deal_rows = [
        {"code": "US.AAPL", "price": 150.0, "order_id": "ORD-A"},
        {"code": "US.MSFT", "qty": 3, "price": 50.0, "order_id": "ORD-B"},
    ]
    fake_mod, created = make_fake_futu_module(deal_rows=deal_rows)
    monkeypatch.setitem(sys.modules, "futu", fake_mod)

    broker = FutuBroker(_settings())
    fills = broker.process_fills(session, D1, {})  # must not raise

    assert len(fills) == 1
    assert fills[0].order_id == order_b.id
    assert get_order(session, order_a.id).status == STATUS_SUBMITTED  # left alone
    assert get_order(session, order_b.id).status == STATUS_FILLED
