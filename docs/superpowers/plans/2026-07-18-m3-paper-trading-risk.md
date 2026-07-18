# M3 模拟盘交易与风控 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 M1/M2 之上打通交易执行链:mode 状态开关落 DB(settings 单例行,唯一真相)、PaperBroker 模拟盘(下一开盘价撮合、DB 持久化)、服务端风控闸门(单票/总仓位上限、单日新开仓上限、日亏损熔断、冷却期)、订单生命周期与模式分流(advisory 只落库 / semi_auto 待确认队列 / full_auto 过闸门直提 PaperBroker)、人工确认流(CLI)、watchdog 心跳降级。半自动/全自动在模拟盘上闭环。**不做**真实券商适配器、**不做** Next.js UI(M4)。

**Architecture:** 分层纪律不变(store → risk/execution 领域模块 → services → mcp/cli 薄壳)。新增 `store/` 五张表(settings/orders/paper_account/paper_positions/paper_fills + heartbeats/alerts)与一实体一仓储文件;`risk/`(rules 一条规则一类 + circuit_breaker + gate)全部纯函数式 check,由 `execution/account_state.py` 从 DB+最新价组装事实;`execution/`(base 抽象 + paper 模拟盘 + order_manager 分流唯一 choke point)。PaperBroker 是 M1 `SimBroker` 的 live 会话版:同一套下一开盘价撮合语义(买按现金截断、卖按持仓截断),状态持久化在 DB,**复用其 Order/Fill 撮合模式,不另起炉灶**。watchdog 为纯函数 + CLI 触发,不引入 APScheduler。

**Tech Stack:** M2 栈不变(Python 3.12 / pandas / numpy / yfinance / pydantic-settings / pyarrow / sqlalchemy / httpx / fastmcp / pytest)。**零新增依赖**(时区用标准库 zoneinfo)。

**设计文档:** `docs/superpowers/specs/2026-07-17-stock-agent-design.md`(§1 模式开关语义、§4 风控与安全、§8 里程碑 M3)

## Global Constraints

**安全红线(M3 的存在意义,逐条有"删了就红"的守卫测试):**

- 风控闸门在服务端确定性执行,LLM/agent 只能建议;full_auto 下任何订单必须先过 gate.check,拒绝即不提交。gate 不可被 payload/工具参数绕过。
- 系统内永不存在转账/出金/提现工具或方法;PaperBroker 只有 buy/sell 撮合,资金永远出不了系统。
- mode 的唯一真相在 DB settings row;decision/order 路径只从 DB 读 mode,不接受调用方传入;未知/未设 → advisory(fail-safe)。
- 日亏损熔断触发后当日只允许卖出;熔断状态持久化,同日重启不重置。
- 全自动开启需显式(DB 标志),watchdog 检测异常自动降级 advisory。
- 沿用 M1/M2 全局约束:≤200 行/文件、离线测试(内存 SQLite,注入时间戳,不联网)、TDD、conventional commits、pytest 从 backend/、uv venv python 3.12、依赖白名单不新增(sqlalchemy/httpx/fastmcp 已在)。

**工程约束:**

- 仓库根:`/data1/common/haibotong/stock-agent`;后端代码在 `backend/` 下,包名 `app`;venv 已建(`backend/.venv`,uv 在 `~/.local/bin/uv`)
- **单文件不超过约 200 行**;`mcp/`、`cli` 是薄壳,业务只写在 services/ 与领域模块(risk/execution/store)
- 每个任务走 TDD:先写失败测试 → 运行确认失败 → 实现 → 运行确认通过 → 提交;提交信息 conventional commits(feat:/test:/chore:),后缀 `(M3 task N)`
- pytest 统一从 `backend/` 运行:`cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest ...`
- **单元测试一律离线**:内存 SQLite(`make_engine(":memory:")`)、假 Provider、注入时间戳(`dt.datetime`/`dt.date` 显式传参);Task 1 起有 autouse socket 屏障兜底
- M2 遗留 backlog 并入本计划:mode 分流集中 order_manager/decision_service 单一 choke point(Task 11/12);orders 按 (as_of, symbol) 活跃单重复保护(Task 4);socket-block conftest(Task 1);as_of 用美东交易日 ET(Task 1/13)
- 复用既有接口,不得重复实现:行情抓取一律走 `app.services.market_data_service.fetch_bars`;撮合语义复用 `app.backtest.sim_broker.SimBroker` 的 Order/Fill 模式(下一开盘价、滑点、现金/持仓截断);落库一律走 `app.store.repos.*`
- 基线:main 上 M1+M2 已合并,149 条测试全绿(3 条 network deselected)

---

### Task 1: ET 交易日纯函数 + 全局 socket 屏障(护栏先行)

**Files:**
- Create: `backend/app/util/__init__.py`(空)
- Create: `backend/app/util/trading_day.py`
- Create: `backend/tests/util/__init__.py`(空)
- Create: `backend/tests/conftest.py`
- Test: `backend/tests/util/test_trading_day.py`
- Test: `backend/tests/test_socket_guard.py`

**Interfaces:**
- Produces: `app.util.trading_day.ET_ZONE = ZoneInfo("America/New_York")`;`et_trading_day(now_utc: dt.datetime) -> dt.date`——UTC 时刻(naive 按 UTC 解释)→ 美东日历日;纯函数,时间由调用方注入
- Produces: `tests.conftest.NetworkBlockedError(RuntimeError)`;autouse fixture `_block_network`——非 `@pytest.mark.network` 测试中对 AF_INET/AF_INET6 的 `socket.connect` 一律抛 `NetworkBlockedError`(AF_UNIX 放行)

- [ ] **Step 1: 写失败测试**

`backend/tests/util/test_trading_day.py`:

```python
import datetime as dt

from app.util.trading_day import ET_ZONE, et_trading_day


def test_summer_evening_utc_is_previous_et_day():
    # 美东夏令时 UTC-4:UTC 7/18 02:00 = ET 7/17 22:00
    assert et_trading_day(dt.datetime(2026, 7, 18, 2, 0, tzinfo=dt.UTC)) == dt.date(2026, 7, 17)


def test_winter_early_utc_is_previous_et_day():
    # 美东标准时 UTC-5:UTC 1/15 04:30 = ET 1/14 23:30
    assert et_trading_day(dt.datetime(2026, 1, 15, 4, 30, tzinfo=dt.UTC)) == dt.date(2026, 1, 14)


def test_afternoon_utc_same_day():
    assert et_trading_day(dt.datetime(2026, 7, 17, 18, 0, tzinfo=dt.UTC)) == dt.date(2026, 7, 17)


def test_naive_datetime_treated_as_utc():
    assert et_trading_day(dt.datetime(2026, 7, 18, 2, 0)) == dt.date(2026, 7, 17)
    assert ET_ZONE.key == "America/New_York"
```

`backend/tests/test_socket_guard.py`:

```python
import socket

import pytest

from tests.conftest import NetworkBlockedError


def test_tcp_connect_is_blocked_by_default():
    # 192.0.2.1 是 TEST-NET 保留地址;屏障应在触网前直接抛错
    with pytest.raises(NetworkBlockedError):
        socket.create_connection(("192.0.2.1", 80), timeout=0.1)


def test_unix_socket_still_allowed(tmp_path):
    # 只拦 AF_INET/AF_INET6;本机 AF_UNIX 不受影响
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(tmp_path / "s.sock"))
    server.listen(1)
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.connect(str(tmp_path / "s.sock"))
    client.close()
    server.close()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/util/test_trading_day.py tests/test_socket_guard.py -v`
Expected: FAIL/ERROR(`ModuleNotFoundError: app.util` 与 `ImportError: cannot import ... tests.conftest`)

- [ ] **Step 3: 实现**

先创建空文件 `backend/app/util/__init__.py` 与 `backend/tests/util/__init__.py`。

`backend/app/util/trading_day.py`:

```python
"""美东交易日纯函数。全系统 as_of 一律用 ET 日历日,终结 host-local date 时区耦合。"""
import datetime as dt
from zoneinfo import ZoneInfo

ET_ZONE = ZoneInfo("America/New_York")


def et_trading_day(now_utc: dt.datetime) -> dt.date:
    """UTC 时刻 → 美东日历日。naive 输入按 UTC 解释;时间由调用方注入,便于测试。"""
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=dt.UTC)
    return now_utc.astimezone(ET_ZONE).date()
```

`backend/tests/conftest.py`:

```python
"""全局测试护栏:非 network 标记的测试一律禁止对外 TCP 连接(防意外联网)。"""
import socket

import pytest

_REAL_CONNECT = socket.socket.connect


class NetworkBlockedError(RuntimeError):
    """单元测试发起了真实网络连接(应 mock,或标 @pytest.mark.network)。"""


@pytest.fixture(autouse=True)
def _block_network(request, monkeypatch):
    if request.node.get_closest_marker("network"):
        yield
        return

    def guarded_connect(self, address, *args, **kwargs):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise NetworkBlockedError(
                f"unit test attempted TCP connect to {address!r}; "
                "mock it or mark the test with @pytest.mark.network")
        return _REAL_CONNECT(self, address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    yield
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/util/test_trading_day.py tests/test_socket_guard.py -v`
Expected: 6 passed

- [ ] **Step 5: 全量回归(确认屏障不误伤既有离线测试)**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest`
Expected: 155 passed, 3 deselected(若有既有测试被屏障拦下,说明它在偷偷联网——修它的 mock,而不是放宽屏障)

- [ ] **Step 6: 提交**

```bash
cd /data1/common/haibotong/stock-agent
git add backend/app/util backend/tests/util backend/tests/conftest.py backend/tests/test_socket_guard.py
git commit -m "feat: ET trading-day util and offline socket guard (M3 task 1)"
```

---

### Task 2: store 模型:settings/orders/模拟盘/心跳/告警表

**Files:**
- Modify: `backend/app/store/models.py`
- Test: `backend/tests/store/test_models_m3.py`

**Interfaces:**
- Produces(全部在 `app.store.models`,沿用既有 `Base`/`_utcnow`):
  - `SettingsRow`(表 `settings`,单例 id=1):`mode: str`(default `"advisory"`)、`single_position_cap_pct: float = 0.20`、`total_position_cap_pct: float = 0.80`、`max_new_positions_per_day: int = 3`、`daily_loss_halt_pct: float = 0.05`、`cooldown_days: int = 5`、`initial_cash: float = 100_000.0`、`updated_at: dt.datetime`
  - `OrderRow`(表 `orders`):`id`、`as_of: dt.date`(索引)、`symbol: str`、`side: str`、`shares: int`、`status: str`(索引)、`mode: str`、`decision_id: int | None`、`reason: str = ""`、`created_at`、`updated_at`
  - `PaperAccountRow`(表 `paper_account`,单例 id=1):`cash: float`、`day_start_date: dt.date | None`、`day_start_equity: float | None`、`breaker_tripped_on: dt.date | None`(熔断持久化字段:同日重启不重置)
  - `PaperPositionRow`(表 `paper_positions`,symbol 唯一):`symbol`、`shares: int`、`avg_cost: float`
  - `PaperFillRow`(表 `paper_fills`):`order_id: int`、`fill_date: dt.date`(索引)、`symbol`、`side`、`shares: int`、`price: float`、`created_at`
  - `HeartbeatRow`(表 `heartbeats`):`job: str`(索引)、`ran_at: dt.datetime`(索引,naive-UTC)、`ok: bool`、`detail: str = ""`
  - `AlertRow`(表 `alerts`):`kind: str`、`message: str`、`created_at`
- 既有表(signals/decisions/reports)不动;`init_db` 的 `create_all` 对已有 DB 自动补建新表,无需迁移脚本

- [ ] **Step 1: 写失败测试**

`backend/tests/store/test_models_m3.py`:

```python
import datetime as dt

from sqlalchemy import inspect, select

from app.store.db import init_db, make_engine, make_session_factory
from app.store.models import (AlertRow, HeartbeatRow, OrderRow, PaperAccountRow,
                              PaperFillRow, PaperPositionRow, SettingsRow)


def _session():
    engine = make_engine(":memory:")
    init_db(engine)
    return make_session_factory(engine)()


def test_init_db_creates_m3_tables():
    engine = make_engine(":memory:")
    init_db(engine)
    tables = set(inspect(engine).get_table_names())
    assert {"settings", "orders", "paper_account", "paper_positions",
            "paper_fills", "heartbeats", "alerts"} <= tables


def test_settings_row_defaults():
    with _session() as session:
        session.add(SettingsRow(id=1))
        session.flush()
        row = session.get(SettingsRow, 1)
        assert row.mode == "advisory"
        assert row.single_position_cap_pct == 0.20
        assert row.total_position_cap_pct == 0.80
        assert row.max_new_positions_per_day == 3
        assert row.daily_loss_halt_pct == 0.05
        assert row.cooldown_days == 5
        assert row.initial_cash == 100_000.0
        assert row.updated_at is not None


def test_order_row_roundtrip_defaults():
    with _session() as session:
        session.add(OrderRow(as_of=dt.date(2026, 7, 17), symbol="AAPL", side="buy",
                             shares=10, status="pending_confirmation", mode="semi_auto"))
        session.commit()
        row = session.scalars(select(OrderRow)).one()
        assert row.id is not None and row.reason == ""
        assert row.decision_id is None
        assert row.created_at is not None and row.updated_at is not None


def test_paper_and_ops_rows_roundtrip():
    with _session() as session:
        session.add(PaperAccountRow(id=1, cash=100_000.0))
        session.add(PaperPositionRow(symbol="AAPL", shares=10, avg_cost=101.0))
        session.add(PaperFillRow(order_id=1, fill_date=dt.date(2026, 7, 20), symbol="AAPL",
                                 side="buy", shares=10, price=101.0))
        session.add(HeartbeatRow(job="premarket_screen", ok=True,
                                 ran_at=dt.datetime(2026, 7, 17, 12, 0)))
        session.add(AlertRow(kind="watchdog_downgrade", message="x"))
        session.commit()
        account = session.get(PaperAccountRow, 1)
        assert account.day_start_date is None
        assert account.day_start_equity is None
        assert account.breaker_tripped_on is None  # 熔断字段默认未触发
        assert session.scalars(select(PaperFillRow)).one().price == 101.0
        assert session.scalars(select(HeartbeatRow)).one().ok is True
        assert session.scalars(select(AlertRow)).one().created_at is not None
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/store/test_models_m3.py -v`
Expected: FAIL(`ImportError: cannot import name 'SettingsRow'`)

- [ ] **Step 3: 实现**

`backend/app/store/models.py` 首行 import 替换为:

```python
from sqlalchemy import (Boolean, Date, DateTime, Float, Integer, String, Text,
                        UniqueConstraint)
```

并在文件末尾追加:

```python
class SettingsRow(Base):
    """运行设置单例行(id 恒为 1)。安全红线:mode 的唯一真相在此,风控参数在此。"""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # 恒为 1
    mode: Mapped[str] = mapped_column(String(16), default="advisory")
    single_position_cap_pct: Mapped[float] = mapped_column(Float, default=0.20)
    total_position_cap_pct: Mapped[float] = mapped_column(Float, default=0.80)
    max_new_positions_per_day: Mapped[int] = mapped_column(Integer, default=3)
    daily_loss_halt_pct: Mapped[float] = mapped_column(Float, default=0.05)
    cooldown_days: Mapped[int] = mapped_column(Integer, default=5)
    initial_cash: Mapped[float] = mapped_column(Float, default=100_000.0)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class OrderRow(Base):
    """订单生命周期。status 见 order_repo.STATUSES;每次拒绝必须写 reason(可审计)。"""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    as_of: Mapped[dt.date] = mapped_column(Date, index=True)
    symbol: Mapped[str] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(String(8))
    shares: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), index=True)
    mode: Mapped[str] = mapped_column(String(16))
    decision_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class PaperAccountRow(Base):
    """模拟盘账户单例行(id 恒为 1)。熔断状态持久化在此:同日重启不重置。"""

    __tablename__ = "paper_account"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # 恒为 1
    cash: Mapped[float] = mapped_column(Float)
    day_start_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    day_start_equity: Mapped[float | None] = mapped_column(Float, nullable=True)
    breaker_tripped_on: Mapped[dt.date | None] = mapped_column(Date, nullable=True)


class PaperPositionRow(Base):
    """模拟盘持仓,一行一标的。"""

    __tablename__ = "paper_positions"
    __table_args__ = (UniqueConstraint("symbol", name="uq_paper_positions_symbol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16))
    shares: Mapped[int] = mapped_column(Integer)
    avg_cost: Mapped[float] = mapped_column(Float)


class PaperFillRow(Base):
    """模拟盘成交流水(冷却期与审计的依据)。"""

    __tablename__ = "paper_fills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(Integer, index=True)
    fill_date: Mapped[dt.date] = mapped_column(Date, index=True)
    symbol: Mapped[str] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(String(8))
    shares: Mapped[int] = mapped_column(Integer)
    price: Mapped[float] = mapped_column(Float)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class HeartbeatRow(Base):
    """cron 心跳(watchdog 依据)。ran_at 为 naive-UTC(与 _utcnow 约定一致)。"""

    __tablename__ = "heartbeats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job: Mapped[str] = mapped_column(String(32), index=True)
    ran_at: Mapped[dt.datetime] = mapped_column(DateTime, index=True)
    ok: Mapped[bool] = mapped_column(Boolean)
    detail: Mapped[str] = mapped_column(Text, default="")


class AlertRow(Base):
    """系统告警(watchdog 降级等),落库可回看。"""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/store/ -v`
Expected: test_models_m3 4 passed;test_db/test_repos 无回归(共 11 passed)

- [ ] **Step 5: 提交**

```bash
cd /data1/common/haibotong/stock-agent
git add backend/app/store/models.py backend/tests/store/test_models_m3.py
git commit -m "feat: M3 store models for settings/orders/paper broker/heartbeats/alerts (M3 task 2)"
```

---

### Task 3: settings_repo:mode 唯一真相 + fail-safe + full_auto 显式开启

**Files:**
- Create: `backend/app/store/repos/settings_repo.py`
- Test: `backend/tests/store/test_settings_repo.py`

**Interfaces:**
- Consumes: `SettingsRow`(Task 2)
- Produces(`app.store.repos.settings_repo`):
  - 常量 `MODE_ADVISORY = "advisory"`、`MODE_SEMI_AUTO = "semi_auto"`、`MODE_FULL_AUTO = "full_auto"`、`MODES = (MODE_ADVISORY, MODE_SEMI_AUTO, MODE_FULL_AUTO)`、`RISK_PARAM_FIELDS`(风控参数白名单,含 `initial_cash`)
  - `get_app_settings(session: Session) -> SettingsRow`(get-or-create 单例 id=1)
  - `get_mode(session: Session) -> str`——**唯一真相**;DB 值未知/为空 → 返回 `MODE_ADVISORY` 并 `logger.warning`(安全红线 fail-safe)
  - `set_mode(session: Session, mode: str, *, confirm_full_auto: bool = False) -> SettingsRow`——mode 不在 MODES 抛 `ValueError`;`full_auto` 未带 `confirm_full_auto=True` 抛 `ValueError`(安全红线:全自动需显式开启)
  - `update_risk_params(session: Session, **fields) -> SettingsRow`——只允许白名单字段(**mode 不在白名单**,只能走 set_mode),未知字段抛 `ValueError`

- [ ] **Step 1: 写失败测试**

`backend/tests/store/test_settings_repo.py`:

```python
import logging

import pytest
from sqlalchemy import select

from app.store.db import init_db, make_engine, make_session_factory
from app.store.models import SettingsRow
from app.store.repos.settings_repo import (MODE_ADVISORY, MODE_FULL_AUTO,
                                           MODE_SEMI_AUTO, MODES, get_app_settings,
                                           get_mode, set_mode, update_risk_params)


@pytest.fixture
def session():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


def test_modes_constant():
    assert MODES == ("advisory", "semi_auto", "full_auto")


def test_get_app_settings_creates_singleton(session):
    row1 = get_app_settings(session)
    row2 = get_app_settings(session)
    assert row1.id == 1 and row2.id == 1
    assert len(session.scalars(select(SettingsRow)).all()) == 1


def test_fresh_db_mode_is_advisory(session):
    # 红线:未设置 → advisory(fail-safe)
    assert get_mode(session) == MODE_ADVISORY


def test_unknown_db_mode_fails_safe(session, caplog):
    # 红线:DB 里出现未知值(手改/脏数据)也必须回落 advisory
    get_app_settings(session).mode = "turbo"
    session.flush()
    with caplog.at_level(logging.WARNING):
        assert get_mode(session) == MODE_ADVISORY
    assert "fail-safe" in caplog.text


def test_set_mode_semi_and_rejects_unknown(session):
    set_mode(session, MODE_SEMI_AUTO)
    assert get_mode(session) == MODE_SEMI_AUTO
    with pytest.raises(ValueError):
        set_mode(session, "yolo")


def test_full_auto_requires_explicit_confirm(session):
    # 红线:全自动开启需显式
    with pytest.raises(ValueError):
        set_mode(session, MODE_FULL_AUTO)
    assert get_mode(session) == MODE_ADVISORY
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    assert get_mode(session) == MODE_FULL_AUTO


def test_update_risk_params_whitelist(session):
    update_risk_params(session, cooldown_days=9, initial_cash=50_000.0)
    row = get_app_settings(session)
    assert row.cooldown_days == 9 and row.initial_cash == 50_000.0
    with pytest.raises(ValueError):
        update_risk_params(session, mode="full_auto")  # mode 不许走参数通道
    with pytest.raises(ValueError):
        update_risk_params(session, evil_field=1)
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/store/test_settings_repo.py -v`
Expected: FAIL(`ModuleNotFoundError: app.store.repos.settings_repo`)

- [ ] **Step 3: 实现**

`backend/app/store/repos/settings_repo.py`:

```python
"""settings 单例行仓储。

安全红线:mode 的唯一真相在 DB settings row;未知/未设 → advisory(fail-safe);
full_auto 必须显式 confirm_full_auto=True 才能开启。
"""
import logging

from sqlalchemy.orm import Session

from app.store.models import SettingsRow

logger = logging.getLogger(__name__)

MODE_ADVISORY = "advisory"
MODE_SEMI_AUTO = "semi_auto"
MODE_FULL_AUTO = "full_auto"
MODES = (MODE_ADVISORY, MODE_SEMI_AUTO, MODE_FULL_AUTO)

RISK_PARAM_FIELDS = ("single_position_cap_pct", "total_position_cap_pct",
                     "max_new_positions_per_day", "daily_loss_halt_pct",
                     "cooldown_days", "initial_cash")


def get_app_settings(session: Session) -> SettingsRow:
    """取(或建)单例行 id=1,字段用模型默认值。"""
    row = session.get(SettingsRow, 1)
    if row is None:
        row = SettingsRow(id=1)
        session.add(row)
        session.flush()
    return row


def get_mode(session: Session) -> str:
    """当前模式(唯一真相)。DB 值未知/为空 → advisory 并告警,绝不抛错。"""
    mode = (get_app_settings(session).mode or "").strip()
    if mode not in MODES:
        logger.warning("settings.mode=%r 非法,fail-safe 降级为 advisory", mode)
        return MODE_ADVISORY
    return mode


def set_mode(session: Session, mode: str, *, confirm_full_auto: bool = False) -> SettingsRow:
    """切换模式。full_auto 需显式 confirm_full_auto=True(安全红线)。"""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if mode == MODE_FULL_AUTO and not confirm_full_auto:
        raise ValueError("enabling full_auto requires confirm_full_auto=True (explicit opt-in)")
    row = get_app_settings(session)
    row.mode = mode
    session.flush()
    return row


def update_risk_params(session: Session, **fields) -> SettingsRow:
    """更新风控参数(白名单);mode 不在白名单,只能走 set_mode。"""
    unknown = set(fields) - set(RISK_PARAM_FIELDS)
    if unknown:
        raise ValueError(f"unknown settings fields: {sorted(unknown)}")
    row = get_app_settings(session)
    for key, value in fields.items():
        setattr(row, key, value)
    session.flush()
    return row
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/store/test_settings_repo.py -v`
Expected: 7 passed

- [ ] **Step 5: 提交**

```bash
cd /data1/common/haibotong/stock-agent
git add backend/app/store/repos/settings_repo.py backend/tests/store/test_settings_repo.py
git commit -m "feat: settings repo as single source of truth for mode with fail-safe advisory (M3 task 3)"
```

---

### Task 4: order_repo:订单仓储 + (as_of, symbol) 活跃单重复保护

**Files:**
- Create: `backend/app/store/repos/order_repo.py`
- Test: `backend/tests/store/test_order_repo.py`

**Interfaces:**
- Consumes: `OrderRow`(Task 2)
- Produces(`app.store.repos.order_repo`):
  - 状态常量 `STATUS_PENDING_CONFIRMATION = "pending_confirmation"`、`STATUS_APPROVED = "approved"`、`STATUS_REJECTED = "rejected"`、`STATUS_SUBMITTED = "submitted"`、`STATUS_FILLED = "filled"`、`STATUS_CANCELLED = "cancelled"`;`STATUSES`(全集)、`ACTIVE_STATUSES = (pending_confirmation, approved, submitted)`、`COUNTED_BUY_STATUSES = ACTIVE_STATUSES + (filled,)`
  - `DuplicateOrderError(ValueError)`
  - `has_active_order(session, as_of: dt.date, symbol: str) -> bool`
  - `create_order(session, as_of: dt.date, symbol: str, side: str, shares: int, status: str, mode: str, decision_id: int | None = None, reason: str = "") -> OrderRow`——status 非法抛 `ValueError`;status 为活跃态且同 (as_of, symbol) 已有活跃单 → 抛 `DuplicateOrderError`(M2 review backlog:防重复下单);**rejected/cancelled 审计单不受重复保护限制**(留痕优先)
  - `get_order(session, order_id: int) -> OrderRow | None`
  - `get_orders_by_status(session, status: str) -> list[OrderRow]`(按 id 升序)
  - `update_status(session, order_id: int, status: str, reason: str = "") -> OrderRow`(status 非法/订单不存在抛 `ValueError`;reason 非空才覆盖)
  - `buy_symbols_today(session, as_of: dt.date) -> set[str]`——当日计入"新开仓数"的买单标的集合(状态 ∈ COUNTED_BUY_STATUSES)

- [ ] **Step 1: 写失败测试**

`backend/tests/store/test_order_repo.py`:

```python
import datetime as dt

import pytest

from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.order_repo import (ACTIVE_STATUSES, STATUS_APPROVED,
                                        STATUS_FILLED, STATUS_PENDING_CONFIRMATION,
                                        STATUS_REJECTED, STATUS_SUBMITTED, STATUSES,
                                        DuplicateOrderError, buy_symbols_today,
                                        create_order, get_order, get_orders_by_status,
                                        has_active_order, update_status)

D = dt.date(2026, 7, 17)
D1 = dt.date(2026, 7, 20)


@pytest.fixture
def session():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


def test_status_constants():
    assert set(ACTIVE_STATUSES) == {"pending_confirmation", "approved", "submitted"}
    assert set(STATUSES) == {"pending_confirmation", "approved", "rejected",
                             "submitted", "filled", "cancelled"}


def test_create_and_get(session):
    row = create_order(session, D, "AAPL", "buy", 10, STATUS_PENDING_CONFIRMATION,
                       "semi_auto", decision_id=7)
    assert row.id is not None
    fetched = get_order(session, row.id)
    assert fetched.symbol == "AAPL" and fetched.status == STATUS_PENDING_CONFIRMATION
    assert fetched.decision_id == 7 and fetched.mode == "semi_auto"
    assert get_order(session, 999) is None
    with pytest.raises(ValueError):
        create_order(session, D, "MSFT", "buy", 10, "weird", "semi_auto")


def test_duplicate_active_order_blocked(session):
    create_order(session, D, "AAPL", "buy", 10, STATUS_PENDING_CONFIRMATION, "semi_auto")
    assert has_active_order(session, D, "AAPL")
    with pytest.raises(DuplicateOrderError):
        create_order(session, D, "AAPL", "sell", 5, STATUS_SUBMITTED, "full_auto")
    # 审计用 rejected 单不受重复保护限制;不同日/不同标的不受影响
    create_order(session, D, "AAPL", "buy", 10, STATUS_REJECTED, "full_auto", reason="over cap")
    create_order(session, D1, "AAPL", "buy", 10, STATUS_PENDING_CONFIRMATION, "semi_auto")
    create_order(session, D, "MSFT", "buy", 10, STATUS_PENDING_CONFIRMATION, "semi_auto")


def test_terminal_order_frees_the_slot(session):
    row = create_order(session, D, "AAPL", "buy", 10, STATUS_PENDING_CONFIRMATION, "semi_auto")
    update_status(session, row.id, STATUS_REJECTED, reason="user")
    assert not has_active_order(session, D, "AAPL")
    create_order(session, D, "AAPL", "buy", 10, STATUS_PENDING_CONFIRMATION, "semi_auto")


def test_update_status_and_reason(session):
    row = create_order(session, D, "AAPL", "buy", 10, STATUS_PENDING_CONFIRMATION, "semi_auto")
    out = update_status(session, row.id, STATUS_APPROVED)
    assert out.status == STATUS_APPROVED and out.reason == ""
    out = update_status(session, row.id, STATUS_REJECTED, reason="risk gate")
    assert out.reason == "risk gate"
    with pytest.raises(ValueError):
        update_status(session, row.id, "weird")
    with pytest.raises(ValueError):
        update_status(session, 999, STATUS_REJECTED)


def test_get_orders_by_status_ordered(session):
    a = create_order(session, D, "AAPL", "buy", 1, STATUS_PENDING_CONFIRMATION, "semi_auto")
    b = create_order(session, D, "MSFT", "buy", 1, STATUS_PENDING_CONFIRMATION, "semi_auto")
    assert [r.id for r in get_orders_by_status(session, STATUS_PENDING_CONFIRMATION)] == [a.id, b.id]
    assert get_orders_by_status(session, STATUS_FILLED) == []


def test_buy_symbols_today_counts_active_and_filled_only(session):
    create_order(session, D, "AAPL", "buy", 1, STATUS_PENDING_CONFIRMATION, "semi_auto")
    create_order(session, D, "MSFT", "buy", 1, STATUS_FILLED, "full_auto")
    create_order(session, D, "NVDA", "buy", 1, STATUS_REJECTED, "full_auto", reason="cap")
    create_order(session, D, "AMD", "sell", 1, STATUS_SUBMITTED, "full_auto")
    create_order(session, D1, "GOOG", "buy", 1, STATUS_SUBMITTED, "full_auto")
    assert buy_symbols_today(session, D) == {"AAPL", "MSFT"}
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/store/test_order_repo.py -v`
Expected: FAIL(`ModuleNotFoundError: app.store.repos.order_repo`)

- [ ] **Step 3: 实现**

`backend/app/store/repos/order_repo.py`:

```python
"""orders 仓储。重复保护:同 (as_of, symbol) 只允许一张活跃订单(防重复下单)。

rejected/cancelled 是终态审计记录,不占用重复保护槽位——拒绝必须留痕。
"""
import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.store.models import OrderRow

STATUS_PENDING_CONFIRMATION = "pending_confirmation"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_SUBMITTED = "submitted"
STATUS_FILLED = "filled"
STATUS_CANCELLED = "cancelled"
STATUSES = (STATUS_PENDING_CONFIRMATION, STATUS_APPROVED, STATUS_REJECTED,
            STATUS_SUBMITTED, STATUS_FILLED, STATUS_CANCELLED)
ACTIVE_STATUSES = (STATUS_PENDING_CONFIRMATION, STATUS_APPROVED, STATUS_SUBMITTED)
COUNTED_BUY_STATUSES = ACTIVE_STATUSES + (STATUS_FILLED,)


class DuplicateOrderError(ValueError):
    """同 (as_of, symbol) 已存在活跃订单。"""


def has_active_order(session: Session, as_of: dt.date, symbol: str) -> bool:
    stmt = (select(OrderRow.id)
            .where(OrderRow.as_of == as_of, OrderRow.symbol == symbol,
                   OrderRow.status.in_(ACTIVE_STATUSES))
            .limit(1))
    return session.scalars(stmt).first() is not None


def create_order(session: Session, as_of: dt.date, symbol: str, side: str, shares: int,
                 status: str, mode: str, decision_id=None, reason: str = "") -> OrderRow:
    if status not in STATUSES:
        raise ValueError(f"invalid status: {status}")
    if status in ACTIVE_STATUSES and has_active_order(session, as_of, symbol):
        raise DuplicateOrderError(f"active order already exists for {symbol} on {as_of}")
    row = OrderRow(as_of=as_of, symbol=symbol, side=side, shares=shares,
                   status=status, mode=mode, decision_id=decision_id, reason=reason)
    session.add(row)
    session.flush()
    return row


def get_order(session: Session, order_id: int):
    return session.get(OrderRow, order_id)


def get_orders_by_status(session: Session, status: str) -> list:
    stmt = select(OrderRow).where(OrderRow.status == status).order_by(OrderRow.id)
    return list(session.scalars(stmt))


def update_status(session: Session, order_id: int, status: str, reason: str = "") -> OrderRow:
    if status not in STATUSES:
        raise ValueError(f"invalid status: {status}")
    row = session.get(OrderRow, order_id)
    if row is None:
        raise ValueError(f"order {order_id} not found")
    row.status = status
    if reason:
        row.reason = reason
    session.flush()
    return row


def buy_symbols_today(session: Session, as_of: dt.date) -> set:
    """当日计入"新开仓数"的买单标的集合(活跃 + 已成交;拒绝/撤销不计)。"""
    stmt = (select(OrderRow.symbol)
            .where(OrderRow.as_of == as_of, OrderRow.side == "buy",
                   OrderRow.status.in_(COUNTED_BUY_STATUSES))
            .distinct())
    return set(session.scalars(stmt))
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/store/test_order_repo.py -v`
Expected: 7 passed

- [ ] **Step 5: 提交**

```bash
cd /data1/common/haibotong/stock-agent
git add backend/app/store/repos/order_repo.py backend/tests/store/test_order_repo.py
git commit -m "feat: order repository with lifecycle statuses and duplicate protection (M3 task 4)"
```

---
### Task 5: paper_repo:模拟盘账户/持仓/成交仓储

**Files:**
- Create: `backend/app/store/repos/paper_repo.py`
- Test: `backend/tests/store/test_paper_repo.py`

**Interfaces:**
- Consumes: `PaperAccountRow`、`PaperPositionRow`、`PaperFillRow`(Task 2)
- Produces(`app.store.repos.paper_repo`):
  - `get_account(session: Session, initial_cash: float) -> PaperAccountRow`——get-or-create 单例 id=1;首建时 `initial_cash <= 0` 抛 `ValueError`;已存在则忽略 `initial_cash`
  - `get_positions(session) -> dict[str, PaperPositionRow]`(仅 shares>0)
  - `set_position(session, symbol: str, shares: int, avg_cost: float) -> None`(upsert;shares ≤ 0 删行)
  - `add_fill(session, order_id: int, fill_date: dt.date, symbol: str, side: str, shares: int, price: float) -> PaperFillRow`
  - `get_fills(session, fill_date: dt.date | None = None) -> list[PaperFillRow]`(按 id 升序)
  - `last_sell_dates(session) -> dict[str, dt.date]`(每标的最近一次卖出成交日,冷却期依据)
- **红线**:本模块(及整个 execution 面)只有 buy/sell 相关读写,没有任何转账/出金/提现方法(Task 9 加全局扫描守卫)

- [ ] **Step 1: 写失败测试**

`backend/tests/store/test_paper_repo.py`:

```python
import datetime as dt

import pytest

from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.paper_repo import (add_fill, get_account, get_fills,
                                        get_positions, last_sell_dates, set_position)

D = dt.date(2026, 7, 17)
D1 = dt.date(2026, 7, 20)


@pytest.fixture
def session():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


def test_get_account_creates_singleton_with_initial_cash(session):
    account = get_account(session, 50_000.0)
    assert account.id == 1 and account.cash == 50_000.0
    assert get_account(session, 999.0).cash == 50_000.0  # 已存在则忽略 initial_cash


def test_get_account_rejects_nonpositive_seed(session):
    with pytest.raises(ValueError):
        get_account(session, 0.0)


def test_set_position_upsert_and_delete(session):
    set_position(session, "AAPL", 10, 100.0)
    set_position(session, "AAPL", 20, 105.0)
    positions = get_positions(session)
    assert positions["AAPL"].shares == 20 and positions["AAPL"].avg_cost == 105.0
    set_position(session, "AAPL", 0, 0.0)
    assert get_positions(session) == {}


def test_add_fill_and_get_fills(session):
    add_fill(session, 1, D, "AAPL", "buy", 10, 100.0)
    add_fill(session, 2, D1, "AAPL", "sell", 5, 110.0)
    assert [f.side for f in get_fills(session)] == ["buy", "sell"]
    assert [f.order_id for f in get_fills(session, D1)] == [2]


def test_last_sell_dates_latest_per_symbol(session):
    add_fill(session, 1, D, "AAPL", "sell", 5, 100.0)
    add_fill(session, 2, D1, "AAPL", "sell", 5, 100.0)
    add_fill(session, 3, D, "MSFT", "buy", 5, 100.0)
    assert last_sell_dates(session) == {"AAPL": D1}
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/store/test_paper_repo.py -v`
Expected: FAIL(`ModuleNotFoundError: app.store.repos.paper_repo`)

- [ ] **Step 3: 实现**

`backend/app/store/repos/paper_repo.py`:

```python
"""模拟盘账户/持仓/成交仓储。

安全红线:资金只在 cash ↔ 持仓之间流转;本模块没有、也永远不会有
转账/出金/提现类方法(tests/execution/test_no_fund_egress.py 全局守卫)。
"""
import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.store.models import PaperAccountRow, PaperFillRow, PaperPositionRow


def get_account(session: Session, initial_cash: float) -> PaperAccountRow:
    """取(或以 initial_cash 建)模拟盘账户单例行 id=1。"""
    row = session.get(PaperAccountRow, 1)
    if row is None:
        if initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        row = PaperAccountRow(id=1, cash=float(initial_cash))
        session.add(row)
        session.flush()
    return row


def get_positions(session: Session) -> dict:
    """symbol -> PaperPositionRow(仅 shares > 0)。"""
    stmt = select(PaperPositionRow).where(PaperPositionRow.shares > 0)
    return {row.symbol: row for row in session.scalars(stmt)}


def set_position(session: Session, symbol: str, shares: int, avg_cost: float) -> None:
    """写持仓(upsert);shares 归零即删行。"""
    stmt = select(PaperPositionRow).where(PaperPositionRow.symbol == symbol)
    row = session.scalars(stmt).first()
    if shares <= 0:
        if row is not None:
            session.delete(row)
    elif row is None:
        session.add(PaperPositionRow(symbol=symbol, shares=shares, avg_cost=float(avg_cost)))
    else:
        row.shares = shares
        row.avg_cost = float(avg_cost)
    session.flush()


def add_fill(session: Session, order_id: int, fill_date: dt.date, symbol: str,
             side: str, shares: int, price: float) -> PaperFillRow:
    row = PaperFillRow(order_id=order_id, fill_date=fill_date, symbol=symbol,
                       side=side, shares=shares, price=float(price))
    session.add(row)
    session.flush()
    return row


def get_fills(session: Session, fill_date: dt.date | None = None) -> list:
    stmt = select(PaperFillRow).order_by(PaperFillRow.id)
    if fill_date is not None:
        stmt = stmt.where(PaperFillRow.fill_date == fill_date)
    return list(session.scalars(stmt))


def last_sell_dates(session: Session) -> dict:
    """symbol -> 最近一次卖出成交日(冷却期规则的依据)。"""
    stmt = select(PaperFillRow).where(PaperFillRow.side == "sell")
    out: dict = {}
    for row in session.scalars(stmt):
        if row.symbol not in out or row.fill_date > out[row.symbol]:
            out[row.symbol] = row.fill_date
    return out
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/store/test_paper_repo.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
cd /data1/common/haibotong/stock-agent
git add backend/app/store/repos/paper_repo.py backend/tests/store/test_paper_repo.py
git commit -m "feat: paper broker account/position/fill repository (M3 task 5)"
```

---

### Task 6: 风控规则(一条规则一个类,纯函数)

**Files:**
- Create: `backend/app/risk/__init__.py`(空)
- Create: `backend/app/risk/rules.py`
- Create: `backend/tests/risk/__init__.py`(空)
- Test: `backend/tests/risk/test_rules.py`

**Interfaces:**
- Produces(`app.risk.rules`,全部 frozen dataclass / 纯 check,不触 DB 不触网):
  - `OrderRequest(symbol: str, side: str, shares: int, price: float, as_of: dt.date)`——price 为服务端取的最新参考价(买入估值用)
  - `AccountState(cash: float, position_values: dict, new_buy_symbols_today: frozenset = frozenset(), last_sell_dates: dict = {}, breaker_tripped: bool = False, stale_priced_symbols: frozenset = frozenset())`,方法 `equity() -> float`(cash + Σ持仓市值)。`stale_priced_symbols` = 持仓中当前无可信报价、只能用 avg_cost 估值的标的集合(权益不可信的信号)
  - `RiskParams(single_position_cap_pct: float, total_position_cap_pct: float, max_new_positions_per_day: int, daily_loss_halt_pct: float, cooldown_days: int)`
  - `RiskCheck(allowed: bool, reason: str)`;常量 `ALLOW = RiskCheck(True, "")`
  - 抽象类 `RiskRule`(类属性 `name: str`,方法 `check(order, account, params) -> RiskCheck`)
  - 六条规则:`CircuitBreakerRule`(熔断日非卖单一律拒)、`StaleQuoteRule`(持仓报价缺失 → 权益不可信 → 买单一律拒,仅允许卖出)、`SinglePositionCapRule`(已持市值+本单估值 > equity×cap 拒)、`TotalPositionCapRule`(总持仓+本单估值 > equity×cap 拒)、`MaxNewPositionsRule`(新开仓标的数达上限拒;已持有/当日已有买单的标的不算新开仓)、`CooldownRule`(卖出后 cooldown_days 个日历日内回买拒)
- 语义约定:卖出只受 CircuitBreakerRule/StaleQuoteRule 之外的规则放行(五条 buy-only 规则对 sell 一律 ALLOW)——"熔断后当日只允许卖出"、"卖只能卖持仓"由 PaperBroker 截断兜底
- **stale-quote fail-safe(评审 finding #6,红线加固)**:持仓标的当前报价缺失(如 yfinance 中途故障),用 avg_cost 估值会**高估权益、低估回撤**,可能让熔断该触发却没触发、且其他标的的买单被放行。因此一旦有持仓报价缺失,`StaleQuoteRule` 保守地把系统降级为"仅允许卖出"——与熔断同姿态。此项在本任务(rules)与 Task 8(gate 装配)、Task 9(account_state 采集 stale)中落地,**不留作 M4**

- [ ] **Step 1: 写失败测试**

`backend/tests/risk/test_rules.py`:

```python
import datetime as dt

import pytest

from app.risk.rules import (ALLOW, AccountState, CircuitBreakerRule, CooldownRule,
                            MaxNewPositionsRule, OrderRequest, RiskCheck, RiskParams,
                            SinglePositionCapRule, StaleQuoteRule, TotalPositionCapRule)

D = dt.date(2026, 7, 17)
PARAMS = RiskParams(single_position_cap_pct=0.20, total_position_cap_pct=0.80,
                    max_new_positions_per_day=2, daily_loss_halt_pct=0.05, cooldown_days=5)


def _account(**overrides):
    fields = dict(cash=100_000.0, position_values={}, new_buy_symbols_today=frozenset(),
                  last_sell_dates={}, breaker_tripped=False, stale_priced_symbols=frozenset())
    fields.update(overrides)
    return AccountState(**fields)


def _buy(symbol="AAPL", shares=10, price=100.0):
    return OrderRequest(symbol=symbol, side="buy", shares=shares, price=price, as_of=D)


def _sell(symbol="AAPL", shares=10, price=100.0):
    return OrderRequest(symbol=symbol, side="sell", shares=shares, price=price, as_of=D)


def test_equity_sums_cash_and_positions():
    acct = _account(cash=1000.0, position_values={"AAPL": 500.0, "MSFT": 250.0})
    assert acct.equity() == pytest.approx(1750.0)


def test_circuit_breaker_blocks_buys_allows_sells():
    # 红线:熔断触发后当日只允许卖出
    rule = CircuitBreakerRule()
    tripped = _account(breaker_tripped=True)
    out = rule.check(_buy(), tripped, PARAMS)
    assert not out.allowed and "circuit breaker" in out.reason
    assert rule.check(_sell(), tripped, PARAMS).allowed
    assert rule.check(_buy(), _account(), PARAMS) is ALLOW


def test_single_position_cap():
    # equity=10 万,cap 20% = 2 万:1.9 万过、2.1 万拒
    rule = SinglePositionCapRule()
    assert rule.check(_buy(shares=190, price=100.0), _account(), PARAMS).allowed
    out = rule.check(_buy(shares=210, price=100.0), _account(), PARAMS)
    assert not out.allowed and "single-position cap" in out.reason


def test_single_position_cap_counts_existing_position():
    # equity=10 万,已持 1.5 万,再买 6 千 → 2.1 万 > 2 万
    acct = _account(cash=85_000.0, position_values={"AAPL": 15_000.0})
    assert not SinglePositionCapRule().check(_buy(shares=60, price=100.0), acct, PARAMS).allowed


def test_total_position_cap():
    # equity=10 万,总仓 cap 80% = 8 万:已持 7.5 万,再买 6 千拒、4 千过
    acct = _account(cash=25_000.0, position_values={"MSFT": 75_000.0})
    out = TotalPositionCapRule().check(_buy(shares=60, price=100.0), acct, PARAMS)
    assert not out.allowed and "total-position cap" in out.reason
    assert TotalPositionCapRule().check(_buy(shares=40, price=100.0), acct, PARAMS).allowed


def test_max_new_positions():
    rule = MaxNewPositionsRule()
    acct = _account(new_buy_symbols_today=frozenset({"MSFT", "NVDA"}))
    out = rule.check(_buy("GOOG"), acct, PARAMS)  # 第 3 个新开仓,超上限 2
    assert not out.allowed and "max new positions" in out.reason
    # 已持有标的加仓不算新开仓
    held = _account(position_values={"AAPL": 5_000.0},
                    new_buy_symbols_today=frozenset({"MSFT", "NVDA"}))
    assert rule.check(_buy("AAPL"), held, PARAMS).allowed
    # 当日已有该标的买单(重复计数保护)不再计新
    assert rule.check(_buy("MSFT"), acct, PARAMS).allowed


def test_cooldown_blocks_rebuy_within_window():
    rule = CooldownRule()
    acct = _account(last_sell_dates={"AAPL": D - dt.timedelta(days=3)})
    out = rule.check(_buy("AAPL"), acct, PARAMS)
    assert not out.allowed and "cooldown" in out.reason
    ok = _account(last_sell_dates={"AAPL": D - dt.timedelta(days=5)})
    assert rule.check(_buy("AAPL"), ok, PARAMS).allowed


def test_sells_bypass_buy_only_rules():
    acct = _account(cash=1_000.0, position_values={"AAPL": 99_000.0},
                    new_buy_symbols_today=frozenset({"A", "B", "C"}),
                    last_sell_dates={"AAPL": D})
    for rule in (SinglePositionCapRule(), TotalPositionCapRule(),
                 MaxNewPositionsRule(), CooldownRule()):
        assert rule.check(_sell("AAPL"), acct, PARAMS).allowed


def test_stale_quote_rule_blocks_buys_allows_sells():
    # 红线加固(finding #6):持仓报价缺失 → 权益不可信 → 保守降级为仅允许卖出。
    # 删掉 StaleQuoteRule 或其 buy 分支,此测试即 fail。
    rule = StaleQuoteRule()
    stale = _account(position_values={"AAPL": 5_000.0},
                     stale_priced_symbols=frozenset({"AAPL"}))
    out = rule.check(_buy("MSFT"), stale, PARAMS)   # 任何标的的买单都拒
    assert not out.allowed and "报价缺失" in out.reason
    assert rule.check(_sell("AAPL"), stale, PARAMS).allowed  # 卖出仍放行
    assert rule.check(_buy("MSFT"), _account(), PARAMS) is ALLOW  # 无 stale 不干预


def test_risk_check_is_frozen():
    with pytest.raises(Exception):
        RiskCheck(True, "").allowed = False
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/risk/test_rules.py -v`
Expected: FAIL(`ModuleNotFoundError: app.risk`)

- [ ] **Step 3: 实现**

先创建空文件 `backend/app/risk/__init__.py` 与 `backend/tests/risk/__init__.py`。

`backend/app/risk/rules.py`:

```python
"""风控规则:一条规则一个类,check 为纯函数(不触 DB、不触网),便于全覆盖单测。

AccountState 由 execution/account_state.py 从 DB + 最新价组装——
规则永远不信任调用方 payload 里的任何数字。
"""
import datetime as dt
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class OrderRequest:
    """闸门评估用的订单请求。price 为服务端取的最新参考价(买入估值必需)。"""

    symbol: str
    side: str  # "buy" | "sell"
    shares: int
    price: float
    as_of: dt.date


@dataclass(frozen=True)
class AccountState:
    """闸门评估用的账户快照。"""

    cash: float
    position_values: dict  # symbol -> 市值
    new_buy_symbols_today: frozenset = frozenset()
    last_sell_dates: dict = field(default_factory=dict)
    breaker_tripped: bool = False
    stale_priced_symbols: frozenset = frozenset()  # 持仓中只能用 avg_cost 估值的标的

    def equity(self) -> float:
        return self.cash + sum(self.position_values.values())


@dataclass(frozen=True)
class RiskParams:
    single_position_cap_pct: float
    total_position_cap_pct: float
    max_new_positions_per_day: int
    daily_loss_halt_pct: float
    cooldown_days: int


@dataclass(frozen=True)
class RiskCheck:
    allowed: bool
    reason: str


ALLOW = RiskCheck(True, "")


class RiskRule(ABC):
    name: str = "risk_rule"

    @abstractmethod
    def check(self, order: OrderRequest, account: AccountState,
              params: RiskParams) -> RiskCheck:
        """允许返回 ALLOW;拒绝返回 RiskCheck(False, 原因)。"""


class CircuitBreakerRule(RiskRule):
    """日亏损熔断:触发当日只允许卖出。"""

    name = "circuit_breaker"

    def check(self, order, account, params):
        if account.breaker_tripped and order.side != "sell":
            return RiskCheck(False, "daily-loss circuit breaker tripped: "
                                    f"only sells allowed on {order.as_of}")
        return ALLOW


class StaleQuoteRule(RiskRule):
    """持仓报价缺失 fail-safe:权益不可信 → 暂停新开仓,仅允许卖出(与熔断同姿态)。

    评审 finding #6:用 avg_cost 顶替缺失报价会高估权益、低估回撤,可能让熔断该触发
    却没触发、且其他标的买单被放行。有持仓报价缺失即保守拒买。
    """

    name = "stale_quote"

    def check(self, order, account, params):
        if order.side == "buy" and account.stale_priced_symbols:
            return RiskCheck(False, "持仓报价缺失,无法可信计算权益,保守起见暂停新开仓"
                                    f"(仅允许卖出);stale={sorted(account.stale_priced_symbols)}")
        return ALLOW


class SinglePositionCapRule(RiskRule):
    """单票市值上限(占权益比例)。"""

    name = "single_position_cap"

    def check(self, order, account, params):
        if order.side != "buy":
            return ALLOW
        target = account.position_values.get(order.symbol, 0.0) + order.shares * order.price
        cap = account.equity() * params.single_position_cap_pct
        if target > cap:
            return RiskCheck(False, f"single-position cap: {order.symbol} "
                                    f"target value {target:.2f} > cap {cap:.2f}")
        return ALLOW


class TotalPositionCapRule(RiskRule):
    """总仓位上限(占权益比例)。"""

    name = "total_position_cap"

    def check(self, order, account, params):
        if order.side != "buy":
            return ALLOW
        target = sum(account.position_values.values()) + order.shares * order.price
        cap = account.equity() * params.total_position_cap_pct
        if target > cap:
            return RiskCheck(False, f"total-position cap: target exposure "
                                    f"{target:.2f} > cap {cap:.2f}")
        return ALLOW


class MaxNewPositionsRule(RiskRule):
    """单日新开仓数上限。已持有标的加仓、当日已计数标的不算新开仓。"""

    name = "max_new_positions"

    def check(self, order, account, params):
        if order.side != "buy":
            return ALLOW
        opening = (order.symbol not in account.position_values
                   and order.symbol not in account.new_buy_symbols_today)
        if opening and len(account.new_buy_symbols_today) >= params.max_new_positions_per_day:
            return RiskCheck(False, "max new positions per day "
                                    f"({params.max_new_positions_per_day}) reached")
        return ALLOW


class CooldownRule(RiskRule):
    """同一标的冷却期:卖出后 cooldown_days 个日历日内不得回买。"""

    name = "cooldown"

    def check(self, order, account, params):
        if order.side != "buy":
            return ALLOW
        last_sell = account.last_sell_dates.get(order.symbol)
        if last_sell is not None and (order.as_of - last_sell).days < params.cooldown_days:
            return RiskCheck(False, f"cooldown: {order.symbol} sold on {last_sell}, "
                                    f"{params.cooldown_days}-day cooldown active")
        return ALLOW
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/risk/test_rules.py -v`
Expected: 10 passed

- [ ] **Step 5: 提交**

```bash
cd /data1/common/haibotong/stock-agent
git add backend/app/risk backend/tests/risk
git commit -m "feat: risk rules one-class-per-rule with pure checks (M3 task 6)"
```

---

### Task 7: 日亏损熔断(持久化,同日重启不重置)

**Files:**
- Create: `backend/app/risk/circuit_breaker.py`
- Test: `backend/tests/risk/test_circuit_breaker.py`

**Interfaces:**
- Consumes: `PaperAccountRow`(Task 2)、`get_account`(Task 5)
- Produces(`app.risk.circuit_breaker`):
  - `should_trip(equity: float, day_start_equity: float, daily_loss_halt_pct: float) -> bool`(纯函数;day_start_equity ≤ 0 → False)
  - `is_tripped(account: PaperAccountRow, as_of: dt.date) -> bool`(`breaker_tripped_on == as_of`;只对触发当日生效,次日自动恢复)
  - `evaluate(session: Session, account: PaperAccountRow, as_of: dt.date, equity: float, daily_loss_halt_pct: float) -> bool`——首见新的一天先把 `day_start_date/day_start_equity` 快照到 DB;已触发直接返回 True(**当日权益回升也不解除**);判定触发则把 `breaker_tripped_on = as_of` 落库并 `logger.warning`
- 安全红线:熔断状态存 `paper_account` 行 → 同日重启(新进程/新 session)读同一 DB,**不重置**

- [ ] **Step 1: 写失败测试**

`backend/tests/risk/test_circuit_breaker.py`:

```python
import datetime as dt

import pytest

from app.risk.circuit_breaker import evaluate, is_tripped, should_trip
from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.paper_repo import get_account

D = dt.date(2026, 7, 17)
D1 = dt.date(2026, 7, 20)


@pytest.fixture
def engine():
    engine = make_engine(":memory:")
    init_db(engine)
    return engine


def test_should_trip_math():
    assert should_trip(equity=94_999.0, day_start_equity=100_000.0, daily_loss_halt_pct=0.05)
    assert not should_trip(equity=95_001.0, day_start_equity=100_000.0, daily_loss_halt_pct=0.05)
    assert not should_trip(equity=100.0, day_start_equity=0.0, daily_loss_halt_pct=0.05)


def test_evaluate_snapshots_day_start_then_trips(engine):
    with make_session_factory(engine)() as session:
        account = get_account(session, 100_000.0)
        assert evaluate(session, account, D, 100_000.0, 0.05) is False
        assert account.day_start_date == D
        assert account.day_start_equity == 100_000.0
        assert evaluate(session, account, D, 94_000.0, 0.05) is True  # 回撤 6% >= 5%
        assert account.breaker_tripped_on == D
        session.commit()


def test_tripped_state_survives_restart_same_day(engine):
    # 红线:熔断状态持久化,同日重启不重置;当日权益回升也不解除
    with make_session_factory(engine)() as session:
        account = get_account(session, 100_000.0)
        evaluate(session, account, D, 100_000.0, 0.05)
        evaluate(session, account, D, 90_000.0, 0.05)
        session.commit()
    with make_session_factory(engine)() as session:  # 模拟重启:同一 DB 新开 session
        account = get_account(session, 100_000.0)
        assert is_tripped(account, D) is True
        assert evaluate(session, account, D, 99_000.0, 0.05) is True


def test_next_day_resets(engine):
    with make_session_factory(engine)() as session:
        account = get_account(session, 100_000.0)
        evaluate(session, account, D, 100_000.0, 0.05)
        evaluate(session, account, D, 90_000.0, 0.05)
        assert evaluate(session, account, D1, 90_000.0, 0.05) is False  # 新一天新基线
        assert account.day_start_equity == 90_000.0
        assert is_tripped(account, D1) is False
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/risk/test_circuit_breaker.py -v`
Expected: FAIL(`ModuleNotFoundError: app.risk.circuit_breaker`)

- [ ] **Step 3: 实现**

`backend/app/risk/circuit_breaker.py`:

```python
"""日亏损熔断:纯判定 + DB 持久化状态。

安全红线:熔断状态存 paper_account 行——同日重启不重置;
触发后当日只允许卖出(由 rules.CircuitBreakerRule 在闸门里执行)。
"""
import datetime as dt
import logging

from sqlalchemy.orm import Session

from app.store.models import PaperAccountRow

logger = logging.getLogger(__name__)


def should_trip(equity: float, day_start_equity: float, daily_loss_halt_pct: float) -> bool:
    """纯函数:当日权益回撤比例 >= 阈值即应熔断。"""
    if day_start_equity <= 0:
        return False
    return (day_start_equity - equity) / day_start_equity >= daily_loss_halt_pct


def is_tripped(account: PaperAccountRow, as_of: dt.date) -> bool:
    """熔断只对触发当日生效(次日自动恢复)。"""
    return account.breaker_tripped_on == as_of


def evaluate(session: Session, account: PaperAccountRow, as_of: dt.date,
             equity: float, daily_loss_halt_pct: float) -> bool:
    """滚动日起点快照 + 熔断判定;返回 as_of 当日是否处于熔断。

    已触发的当日即使权益回升也不解除(防抖动反复开闸)。
    """
    if account.day_start_date != as_of:
        account.day_start_date = as_of
        account.day_start_equity = float(equity)
        session.flush()
    if is_tripped(account, as_of):
        return True
    if should_trip(equity, account.day_start_equity, daily_loss_halt_pct):
        account.breaker_tripped_on = as_of
        session.flush()
        logger.warning("daily-loss circuit breaker tripped on %s (equity %.2f, day start %.2f)",
                       as_of, equity, account.day_start_equity)
        return True
    return False
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/risk/test_circuit_breaker.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
cd /data1/common/haibotong/stock-agent
git add backend/app/risk/circuit_breaker.py backend/tests/risk/test_circuit_breaker.py
git commit -m "feat: persistent daily-loss circuit breaker surviving same-day restarts (M3 task 7)"
```

---

### Task 8: 风控闸门 RiskGate(default-deny + 拒绝必留痕)

**Files:**
- Create: `backend/app/risk/gate.py`
- Test: `backend/tests/risk/test_gate.py`

**Interfaces:**
- Consumes: Task 6 全部导出;`SettingsRow`(Task 2)
- Produces(`app.risk.gate`):
  - `DEFAULT_RULES = (CircuitBreakerRule(), StaleQuoteRule(), SinglePositionCapRule(), TotalPositionCapRule(), MaxNewPositionsRule(), CooldownRule())`(熔断最先,stale-quote fail-safe 紧随其后——权益不可信时先于任何按权益估值的 cap 规则拦下买单)
  - `params_from_row(row: SettingsRow) -> RiskParams`(DB 行 → 纯参数对象)
  - `RiskGate(rules: tuple = DEFAULT_RULES)`,方法 `check(order: OrderRequest, account: AccountState, params: RiskParams) -> RiskCheck`——先 sanity(default-deny:side 非 buy/sell、shares ≤ 0、买单 price ≤ 0 一律拒),再顺序执行规则,第一条拒绝即终止;**每次拒绝 `logger.warning` 留痕**
- 安全红线:gate 的输入只有服务端组装的 OrderRequest/AccountState/RiskParams——payload/工具参数没有任何通道进入判定

- [ ] **Step 1: 写失败测试**

`backend/tests/risk/test_gate.py`:

```python
import datetime as dt
import logging

from app.risk.gate import DEFAULT_RULES, RiskGate, params_from_row
from app.risk.rules import AccountState, OrderRequest, RiskParams
from app.store.models import SettingsRow

D = dt.date(2026, 7, 17)
PARAMS = RiskParams(single_position_cap_pct=0.20, total_position_cap_pct=0.80,
                    max_new_positions_per_day=3, daily_loss_halt_pct=0.05, cooldown_days=5)


def _account(**overrides):
    fields = dict(cash=100_000.0, position_values={}, new_buy_symbols_today=frozenset(),
                  last_sell_dates={}, breaker_tripped=False, stale_priced_symbols=frozenset())
    fields.update(overrides)
    return AccountState(**fields)


def _order(side="buy", shares=10, price=100.0, symbol="AAPL"):
    return OrderRequest(symbol=symbol, side=side, shares=shares, price=price, as_of=D)


def test_default_rules_cover_all_six():
    assert [rule.name for rule in DEFAULT_RULES] == [
        "circuit_breaker", "stale_quote", "single_position_cap", "total_position_cap",
        "max_new_positions", "cooldown"]


def test_allows_normal_buy():
    assert RiskGate().check(_order(), _account(), PARAMS).allowed


def test_rejects_over_cap_and_logs(caplog):
    # 红线:拒绝必须留痕
    with caplog.at_level(logging.WARNING):
        out = RiskGate().check(_order(shares=300), _account(), PARAMS)
    assert not out.allowed and "single-position cap" in out.reason
    assert "risk gate rejected" in caplog.text


def test_default_deny_invalid_side():
    out = RiskGate().check(_order(side="short"), _account(), PARAMS)
    assert not out.allowed and "denied by default" in out.reason


def test_default_deny_nonpositive_shares():
    assert not RiskGate().check(_order(shares=0), _account(), PARAMS).allowed


def test_default_deny_buy_without_price():
    # 缺参考价的买单 fail-safe 拒绝(而不是按 0 元估值放行)
    out = RiskGate().check(_order(price=0.0), _account(), PARAMS)
    assert not out.allowed and "price" in out.reason


def test_first_rejection_wins_breaker_first():
    tripped = _account(breaker_tripped=True)
    out = RiskGate().check(_order(shares=300), tripped, PARAMS)
    assert "circuit breaker" in out.reason


def test_params_from_row_maps_all_fields():
    row = SettingsRow(id=1, single_position_cap_pct=0.1, total_position_cap_pct=0.5,
                      max_new_positions_per_day=1, daily_loss_halt_pct=0.02, cooldown_days=9)
    assert params_from_row(row) == RiskParams(0.1, 0.5, 1, 0.02, 9)
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/risk/test_gate.py -v`
Expected: FAIL(`ModuleNotFoundError: app.risk.gate`)

- [ ] **Step 3: 实现**

`backend/app/risk/gate.py`:

```python
"""风控闸门:服务端确定性执行,LLM/调用方只能"建议"。

安全红线:
- full_auto 下任何订单必须先过 gate.check,拒绝即不提交;
- gate 的输入只有服务端组装的 OrderRequest/AccountState/RiskParams,
  payload/工具参数没有任何通道进入判定,不可被绕过;
- 非法输入默认拒绝(default-deny);每次拒绝 logger.warning 留痕。
"""
import logging

from app.risk.rules import (ALLOW, AccountState, CircuitBreakerRule, CooldownRule,
                            MaxNewPositionsRule, OrderRequest, RiskCheck, RiskParams,
                            SinglePositionCapRule, StaleQuoteRule, TotalPositionCapRule)
from app.store.models import SettingsRow

logger = logging.getLogger(__name__)

DEFAULT_RULES = (CircuitBreakerRule(), StaleQuoteRule(), SinglePositionCapRule(),
                 TotalPositionCapRule(), MaxNewPositionsRule(), CooldownRule())


def params_from_row(row: SettingsRow) -> RiskParams:
    """DB settings 行 → 纯参数对象(规则层不接触 ORM)。"""
    return RiskParams(
        single_position_cap_pct=row.single_position_cap_pct,
        total_position_cap_pct=row.total_position_cap_pct,
        max_new_positions_per_day=row.max_new_positions_per_day,
        daily_loss_halt_pct=row.daily_loss_halt_pct,
        cooldown_days=row.cooldown_days,
    )


class RiskGate:
    """顺序执行所有 RiskRule,第一条拒绝即终止(熔断最先)。"""

    def __init__(self, rules: tuple = DEFAULT_RULES):
        self._rules = rules

    def check(self, order: OrderRequest, account: AccountState,
              params: RiskParams) -> RiskCheck:
        result = self._sanity(order)
        if result.allowed:
            for rule in self._rules:
                result = rule.check(order, account, params)
                if not result.allowed:
                    break
        if not result.allowed:
            logger.warning("risk gate rejected %s %s x%s: %s",
                           order.side, order.symbol, order.shares, result.reason)
        return result

    @staticmethod
    def _sanity(order: OrderRequest) -> RiskCheck:
        """default-deny:side/shares 非法、买单缺参考价,一律拒绝。"""
        if order.side not in ("buy", "sell"):
            return RiskCheck(False, f"invalid side {order.side!r}: denied by default")
        if order.shares <= 0:
            return RiskCheck(False, "shares must be positive: denied by default")
        if order.side == "buy" and order.price <= 0:
            return RiskCheck(False, "buy requires a positive reference price: denied by default")
        return ALLOW
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/risk/ -v`
Expected: test_gate 8 passed(risk/ 共 22 passed:test_rules 10 + test_circuit_breaker 4 + test_gate 8)

- [ ] **Step 5: 提交**

```bash
cd /data1/common/haibotong/stock-agent
git add backend/app/risk/gate.py backend/tests/risk/test_gate.py
git commit -m "feat: server-side risk gate with default-deny and rejection logging (M3 task 8)"
```

---
### Task 9: Broker 抽象 + 账户快照组装 + "资金无出口"全局守卫

**Files:**
- Create: `backend/app/execution/__init__.py`(空)
- Create: `backend/app/execution/base.py`
- Create: `backend/app/execution/account_state.py`
- Create: `backend/tests/execution/__init__.py`(空)
- Test: `backend/tests/execution/test_account_state.py`
- Test: `backend/tests/execution/test_no_fund_egress.py`

**Interfaces:**
- Consumes: `AccountState`(Task 6)、`evaluate`(Task 7)、`get_app_settings`(Task 3)、`buy_symbols_today`(Task 4)、`get_account/get_positions/last_sell_dates`(Task 5)、`OrderRow`(Task 2)
- Produces(`app.execution.base`):抽象类 `Broker`,仅两个抽象方法:`submit(session: Session, order: OrderRow) -> OrderRow`、`process_fills(session: Session, fill_date: dt.date, open_prices: dict) -> list`——**接口层面就不存在任何资金转出方法**
- Produces(`app.execution.account_state`):`build_account_state(session: Session, as_of: dt.date, prices: dict) -> AccountState`——持仓市值 = shares × 最新价(**缺价的持仓**:仍用 avg_cost 估值以出显示值,但其 symbol 收进 `stale_priced_symbols`——finding #6,权益不可信信号);equity = cash + Σ市值;在此统一调用 `circuit_breaker.evaluate`(快照日起点、判定并持久化熔断);`new_buy_symbols_today`/`last_sell_dates`/`stale_priced_symbols` 从 DB + prices 组装
- 安全红线守卫(tests/execution/test_no_fund_egress.py):扫描 `backend/app/` 全部 `.py` 的函数/方法定义名,`transfer/withdraw/deposit/payout/wire_/move_funds/send_funds` 任一出现即 FAIL;`Broker` 接口属性同查——**谁往系统里加出金方法,这个测试就红**

- [ ] **Step 1: 写失败测试**

`backend/tests/execution/test_account_state.py`:

```python
import datetime as dt

import pytest

from app.execution.account_state import build_account_state
from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.order_repo import STATUS_PENDING_CONFIRMATION, create_order
from app.store.repos.paper_repo import add_fill, get_account, set_position
from app.store.repos.settings_repo import update_risk_params

D = dt.date(2026, 7, 17)


@pytest.fixture
def session():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


def test_positions_valued_at_latest_price_with_avg_cost_fallback(session):
    set_position(session, "AAPL", 10, 90.0)
    set_position(session, "MSFT", 2, 50.0)
    state = build_account_state(session, D, {"AAPL": 100.0})
    assert state.position_values["AAPL"] == pytest.approx(1000.0)
    assert state.position_values["MSFT"] == pytest.approx(100.0)  # 缺价保守用 avg_cost
    assert state.equity() == pytest.approx(100_000.0 + 1100.0)
    # finding #6:报价缺失的持仓被采集进 stale_priced_symbols(供 StaleQuoteRule 拦买单)
    assert state.stale_priced_symbols == frozenset({"MSFT"})


def test_cash_seeded_from_settings_initial_cash(session):
    update_risk_params(session, initial_cash=50_000.0)
    state = build_account_state(session, D, {})
    assert state.cash == 50_000.0


def test_breaker_evaluated_and_persisted(session):
    set_position(session, "AAPL", 100, 100.0)
    first = build_account_state(session, D, {"AAPL": 100.0})   # day start = 110_000
    assert first.breaker_tripped is False
    crashed = build_account_state(session, D, {"AAPL": 20.0})  # 权益 102_000,回撤 7.3%
    assert crashed.breaker_tripped is True
    assert get_account(session, 100_000.0).breaker_tripped_on == D


def test_new_buy_symbols_and_last_sell_dates_wired(session):
    create_order(session, D, "NVDA", "buy", 5, STATUS_PENDING_CONFIRMATION, "semi_auto")
    add_fill(session, 1, D, "AMD", "sell", 3, 10.0)
    state = build_account_state(session, D, {})
    assert state.new_buy_symbols_today == frozenset({"NVDA"})
    assert state.last_sell_dates == {"AMD": D}
```

`backend/tests/execution/test_no_fund_egress.py`:

```python
"""安全红线守卫:系统内永不存在转账/出金/提现方法。有人加了,这里必须红。"""
import re
from pathlib import Path

from app.execution.base import Broker

APP_DIR = Path(__file__).resolve().parents[2] / "app"
FORBIDDEN = ("transfer", "withdraw", "deposit", "payout", "wire_",
             "move_funds", "send_funds")
DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_0-9]+)", re.MULTILINE)


def test_broker_interface_has_no_fund_egress():
    names = {n.lower() for n in dir(Broker)}
    hits = [n for n in names for bad in FORBIDDEN if bad in n]
    assert hits == []


def test_no_app_module_defines_fund_egress_functions():
    offenders = []
    for path in sorted(APP_DIR.rglob("*.py")):
        for name in DEF_RE.findall(path.read_text(encoding="utf-8")):
            if any(bad in name.lower() for bad in FORBIDDEN):
                offenders.append(f"{path.relative_to(APP_DIR)}:{name}")
    assert offenders == [], f"fund-egress function detected: {offenders}"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/execution/ -v`
Expected: FAIL(`ModuleNotFoundError: app.execution`)

- [ ] **Step 3: 实现**

先创建空文件 `backend/app/execution/__init__.py` 与 `backend/tests/execution/__init__.py`。

`backend/app/execution/base.py`:

```python
"""Broker 抽象。

安全红线:接口只有 buy/sell 订单的提交与撮合——系统内永不存在
转账/出金/提现方法(tests/execution/test_no_fund_egress.py 守卫)。
"""
import datetime as dt
from abc import ABC, abstractmethod

from sqlalchemy.orm import Session

from app.store.models import OrderRow


class Broker(ABC):
    """订单执行抽象:M3 只有 PaperBroker;M4+ 券商适配器实现同一接口。"""

    @abstractmethod
    def submit(self, session: Session, order: OrderRow) -> OrderRow:
        """把已获准订单标记为 submitted,等待下一交易时段开盘撮合。"""

    @abstractmethod
    def process_fills(self, session: Session, fill_date: dt.date, open_prices: dict) -> list:
        """用 fill_date 开盘价撮合所有 submitted 订单,返回成交(PaperFillRow)列表。"""
```

`backend/app/execution/account_state.py`:

```python
"""从 DB + 最新价组装 AccountState——闸门判定的唯一事实来源。

安全红线:这里的每个数字都来自服务端(DB 持仓/现金/订单流水 + 服务端取价),
绝不采信调用方 payload;熔断在此统一评估并持久化。
"""
import datetime as dt

from sqlalchemy.orm import Session

from app.risk.circuit_breaker import evaluate
from app.risk.rules import AccountState
from app.store.repos.order_repo import buy_symbols_today
from app.store.repos.paper_repo import get_account, get_positions, last_sell_dates
from app.store.repos.settings_repo import get_app_settings


def build_account_state(session: Session, as_of: dt.date, prices: dict) -> AccountState:
    """持仓市值用最新价;缺价的持仓仍用 avg_cost 估值但记入 stale_priced_symbols
    (finding #6:权益不可信信号,StaleQuoteRule 据此拦买单);顺带完成熔断评估。"""
    settings_row = get_app_settings(session)
    account = get_account(session, settings_row.initial_cash)
    position_values = {}
    stale = []
    for symbol, row in get_positions(session).items():
        if symbol in prices:
            position_values[symbol] = row.shares * float(prices[symbol])
        else:
            position_values[symbol] = row.shares * float(row.avg_cost)
            stale.append(symbol)
    equity = account.cash + sum(position_values.values())
    tripped = evaluate(session, account, as_of, equity, settings_row.daily_loss_halt_pct)
    return AccountState(
        cash=account.cash,
        position_values=position_values,
        new_buy_symbols_today=frozenset(buy_symbols_today(session, as_of)),
        last_sell_dates=last_sell_dates(session),
        breaker_tripped=tripped,
        stale_priced_symbols=frozenset(stale),
    )
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/execution/ -v`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
cd /data1/common/haibotong/stock-agent
git add backend/app/execution backend/tests/execution
git commit -m "feat: broker abstraction, account-state builder and fund-egress guard test (M3 task 9)"
```

---

### Task 10: PaperBroker(SimBroker 语义的 DB 持久化 live 版)

**Files:**
- Create: `backend/app/execution/paper.py`
- Test: `backend/tests/execution/test_paper.py`

**Interfaces:**
- Consumes: `Broker`(Task 9)、`order_repo`(Task 4)、`paper_repo`(Task 5)、`get_app_settings`(Task 3)
- Produces(`app.execution.paper`):`PaperBroker(slippage_bps: float = 5.0)`,实现 `Broker`:
  - `submit(session, order: OrderRow) -> OrderRow`——side/shares 非法抛 `ValueError`(与 `SimBroker.submit` 同校验);状态 → `submitted`
  - `process_fills(session, fill_date: dt.date, open_prices: dict) -> list[PaperFillRow]`——撮合语义**逐条复用 SimBroker**:买入价 `open*(1+slip)`、卖出价 `open*(1-slip)`;买入按现金截断股数、卖出按持仓截断;截断后为 0 → 订单 `cancelled` + reason(比 SimBroker 静默丢弃更进一步:live 订单必须留痕);无开盘价 → `cancelled` + reason;成交则订单 → `filled`、写 `paper_fills`、更新 cash 与持仓(买入重算 avg_cost,卖出保持 avg_cost)
- 与 SimBroker 的分工:SimBroker 服务回测(内存态、engine.py 专用,**不动它**);PaperBroker 服务 live 模拟盘(DB 态、跨进程持久)。两者撮合口径一致,回测结论可迁移

- [ ] **Step 1: 写失败测试**

`backend/tests/execution/test_paper.py`:

```python
import datetime as dt

import pytest

from app.execution.paper import PaperBroker
from app.store.db import init_db, make_engine, make_session_factory
from app.store.models import OrderRow
from app.store.repos.order_repo import (STATUS_APPROVED, STATUS_CANCELLED,
                                        STATUS_FILLED, STATUS_SUBMITTED,
                                        create_order, get_order)
from app.store.repos.paper_repo import get_account, get_fills, get_positions, set_position
from app.store.repos.settings_repo import update_risk_params

D = dt.date(2026, 7, 17)
D1 = dt.date(2026, 7, 20)


@pytest.fixture
def engine():
    engine = make_engine(":memory:")
    init_db(engine)
    return engine


@pytest.fixture
def session(engine):
    with make_session_factory(engine)() as s:
        yield s


def _submitted(session, symbol="AAPL", side="buy", shares=10):
    row = create_order(session, D, symbol, side, shares, STATUS_APPROVED, "full_auto")
    return PaperBroker().submit(session, row)


def test_submit_marks_submitted_and_validates(session):
    row = _submitted(session)
    assert row.status == STATUS_SUBMITTED
    with pytest.raises(ValueError):
        PaperBroker().submit(session, OrderRow(as_of=D, symbol="X", side="hold",
                                               shares=1, status=STATUS_APPROVED, mode="full_auto"))
    with pytest.raises(ValueError):
        PaperBroker().submit(session, OrderRow(as_of=D, symbol="X", side="buy",
                                               shares=0, status=STATUS_APPROVED, mode="full_auto"))


def test_buy_fills_next_open_with_slippage(session):
    row = _submitted(session, shares=10)
    fills = PaperBroker(slippage_bps=100).process_fills(session, D1, {"AAPL": 100.0})
    session.commit()
    assert len(fills) == 1 and fills[0].shares == 10
    assert fills[0].price == pytest.approx(101.0)  # 1% 滑点
    assert get_order(session, row.id).status == STATUS_FILLED
    assert get_account(session, 100_000.0).cash == pytest.approx(100_000.0 - 1_010.0)
    position = get_positions(session)["AAPL"]
    assert position.shares == 10 and position.avg_cost == pytest.approx(101.0)


def test_buy_clamps_to_cash(session):
    update_risk_params(session, initial_cash=500.0)
    row = _submitted(session, shares=10)
    fills = PaperBroker(slippage_bps=100).process_fills(session, D1, {"AAPL": 100.0})
    assert fills[0].shares == 4  # int(500 // 101)
    assert get_order(session, row.id).status == STATUS_FILLED


def test_buy_with_no_cash_cancelled_with_reason(session):
    update_risk_params(session, initial_cash=50.0)
    row = _submitted(session, shares=1)
    assert PaperBroker(slippage_bps=100).process_fills(session, D1, {"AAPL": 100.0}) == []
    out = get_order(session, row.id)
    assert out.status == STATUS_CANCELLED and "insufficient cash" in out.reason


def test_sell_clamps_to_position(session):
    set_position(session, "AAPL", 3, 90.0)
    _submitted(session, side="sell", shares=5)
    fills = PaperBroker(slippage_bps=0).process_fills(session, D1, {"AAPL": 110.0})
    assert fills[0].shares == 3  # 卖出按持仓截断
    assert get_positions(session) == {}
    assert get_account(session, 100_000.0).cash == pytest.approx(100_000.0 + 330.0)


def test_sell_without_position_cancelled(session):
    row = _submitted(session, side="sell", shares=5)
    assert PaperBroker().process_fills(session, D1, {"AAPL": 100.0}) == []
    out = get_order(session, row.id)
    assert out.status == STATUS_CANCELLED and "no position" in out.reason


def test_missing_open_price_cancelled_with_reason(session):
    row = _submitted(session)
    assert PaperBroker().process_fills(session, D1, {}) == []
    out = get_order(session, row.id)
    assert out.status == STATUS_CANCELLED and "no open price" in out.reason


def test_avg_cost_recomputed_on_second_buy(session):
    set_position(session, "AAPL", 10, 100.0)
    _submitted(session, shares=10)
    PaperBroker(slippage_bps=0).process_fills(session, D1, {"AAPL": 120.0})
    position = get_positions(session)["AAPL"]
    assert position.shares == 20 and position.avg_cost == pytest.approx(110.0)


def test_state_survives_restart(engine):
    with make_session_factory(engine)() as session:
        _submitted(session, shares=10)
        PaperBroker(slippage_bps=0).process_fills(session, D1, {"AAPL": 100.0})
        session.commit()
    with make_session_factory(engine)() as session:  # 模拟重启
        assert get_positions(session)["AAPL"].shares == 10
        assert get_account(session, 100_000.0).cash == pytest.approx(99_000.0)
        assert len(get_fills(session)) == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/execution/test_paper.py -v`
Expected: FAIL(`ModuleNotFoundError: app.execution.paper`)

- [ ] **Step 3: 实现**

`backend/app/execution/paper.py`:

```python
"""自建模拟盘:SimBroker 的 live 会话版——同一套下一开盘价撮合语义,状态持久化在 DB。

安全红线:只有 buy/sell 撮合;买入按现金截断、卖出按持仓截断;
资金只在 cash ↔ 持仓间流转,没有任何离开系统的路径。
"""
import datetime as dt
import logging

from sqlalchemy.orm import Session

from app.execution.base import Broker
from app.store.models import OrderRow
from app.store.repos import order_repo, paper_repo
from app.store.repos.settings_repo import get_app_settings

logger = logging.getLogger(__name__)


class PaperBroker(Broker):
    """T 日 submit 的订单在下一交易时段 process_fills(开盘价)成交,同 SimBroker 语义。"""

    def __init__(self, slippage_bps: float = 5.0):
        self._slip = slippage_bps / 10_000

    def submit(self, session: Session, order: OrderRow) -> OrderRow:
        if order.side not in ("buy", "sell"):
            raise ValueError(f"invalid side: {order.side}")
        if order.shares <= 0:
            raise ValueError("shares must be positive")
        return order_repo.update_status(session, order.id, order_repo.STATUS_SUBMITTED)

    def process_fills(self, session: Session, fill_date: dt.date, open_prices: dict) -> list:
        """撮合所有 submitted 订单;无法成交的一律 cancelled + reason(留痕,不静默)。"""
        account = paper_repo.get_account(session, get_app_settings(session).initial_cash)
        fills = []
        for order in order_repo.get_orders_by_status(session, order_repo.STATUS_SUBMITTED):
            price = open_prices.get(order.symbol)
            if price is None:
                order_repo.update_status(session, order.id, order_repo.STATUS_CANCELLED,
                                         reason=f"no open price on {fill_date}")
                continue
            fill = self._execute(session, account, order, fill_date, float(price))
            if fill is not None:
                fills.append(fill)
        session.flush()
        return fills

    def _execute(self, session, account, order: OrderRow, fill_date: dt.date,
                 open_price: float):
        held = paper_repo.get_positions(session).get(order.symbol)
        held_shares = held.shares if held is not None else 0
        if order.side == "buy":
            price = open_price * (1 + self._slip)
            shares = min(order.shares, int(account.cash // price))
            if shares <= 0:
                order_repo.update_status(session, order.id, order_repo.STATUS_CANCELLED,
                                         reason="insufficient cash at fill time")
                return None
            account.cash -= shares * price
            prev_cost = held.avg_cost * held_shares if held is not None else 0.0
            total = held_shares + shares
            paper_repo.set_position(session, order.symbol, total,
                                    (prev_cost + shares * price) / total)
        else:
            price = open_price * (1 - self._slip)
            shares = min(order.shares, held_shares)
            if shares <= 0:
                order_repo.update_status(session, order.id, order_repo.STATUS_CANCELLED,
                                         reason="no position to sell at fill time")
                return None
            account.cash += shares * price
            paper_repo.set_position(session, order.symbol, held_shares - shares,
                                    held.avg_cost if held is not None else 0.0)
        order_repo.update_status(session, order.id, order_repo.STATUS_FILLED,
                                 reason=f"filled {shares} @ {price:.4f} on {fill_date}")
        return paper_repo.add_fill(session, order.id, fill_date, order.symbol,
                                   order.side, shares, price)
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/execution/ -v`
Expected: test_paper 9 passed(execution/ 共 15 passed)

- [ ] **Step 5: 提交**

```bash
cd /data1/common/haibotong/stock-agent
git add backend/app/execution/paper.py backend/tests/execution/test_paper.py
git commit -m "feat: DB-persisted paper broker with next-open fill semantics (M3 task 10)"
```

---

### Task 11: order_manager:模式分流唯一 choke point + 确认流

**Files:**
- Create: `backend/app/execution/order_manager.py`
- Test: `backend/tests/execution/test_order_manager.py`

**Interfaces:**
- Consumes: `build_account_state`(Task 9)、`PaperBroker`(Task 10)、`RiskGate/params_from_row`(Task 8)、`OrderRequest`(Task 6)、`order_repo`(Task 4)、`get_app_settings/MODE_SEMI_AUTO/MODE_FULL_AUTO`(Task 3)、`DecisionRow/OrderRow`(M2/Task 2)
- Produces(`app.execution.order_manager`,全系统**唯一**的下单入口——M2 review backlog):
  - `order_to_dict(row: OrderRow) -> dict`(键:`id/as_of/symbol/side/shares/status/mode/reason/decision_id`;`as_of` 为 ISO 串)
  - `handle_decision(session, decision: DecisionRow, mode: str, shares: int, prices: dict) -> dict`——返回 `{"order": dict | None, "note": str}`;mode 非 semi/full → 不建单(fail-safe);同 (as_of, symbol) 有活跃单 → 不建单 + note("duplicate protection")+ warning;**先过闸门**:拒 → 建 `rejected` 审计单(reason=闸门原因);过 → semi_auto 建 `pending_confirmation`,full_auto 建 `approved` 并立即 `PaperBroker.submit` → `submitted`
  - `list_pending(session) -> list[dict]`
  - `approve_order(session, order_id: int, as_of: dt.date, prices: dict) -> dict`——仅 `pending_confirmation` 可批;**批准时刻用最新 AccountState/参数重新过闸门**,拒 → `rejected`(reason 前缀 "rejected at approval")、过 → `approved` + broker.submit → `submitted`
  - `reject_order(session, order_id: int, reason: str = "rejected by user") -> dict`
  - `settle_open(session, fill_date: dt.date, open_prices: dict) -> list[dict]`(键:`order_id/symbol/side/shares/price/fill_date`)
- 本模块不 commit(事务边界在 service/CLI 层,与 M2 约定一致)

- [ ] **Step 1: 写失败测试**

`backend/tests/execution/test_order_manager.py`:

```python
import datetime as dt

import pytest

from app.execution.order_manager import (approve_order, handle_decision, list_pending,
                                         order_to_dict, reject_order, settle_open)
from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.decision_repo import save_decision
from app.store.repos.order_repo import (STATUS_FILLED, STATUS_PENDING_CONFIRMATION,
                                        STATUS_REJECTED, STATUS_SUBMITTED, get_order)
from app.store.repos.paper_repo import get_account, get_positions
from app.store.repos.settings_repo import (MODE_FULL_AUTO, MODE_SEMI_AUTO,
                                           update_risk_params)

D = dt.date(2026, 7, 17)
D1 = dt.date(2026, 7, 20)
PRICES = {"AAPL": 100.0}


@pytest.fixture
def session():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


def _decision(session, symbol="AAPL", action="buy"):
    return save_decision(session, D, symbol, action, 0.8, "semi_auto", "{}")


def test_semi_auto_queues_pending(session):
    out = handle_decision(session, _decision(session), MODE_SEMI_AUTO, 10, PRICES)
    assert out["order"]["status"] == STATUS_PENDING_CONFIRMATION
    assert out["order"]["decision_id"] is not None
    assert [o["id"] for o in list_pending(session)] == [out["order"]["id"]]


def test_full_auto_within_caps_submits(session):
    out = handle_decision(session, _decision(session), MODE_FULL_AUTO, 10, PRICES)
    assert out["order"]["status"] == STATUS_SUBMITTED


def test_full_auto_over_cap_rejected_not_submitted(session):
    # 红线:full_auto 超单票上限(20% × 10 万 = 2 万)必须被闸门拒绝,不提交 broker
    out = handle_decision(session, _decision(session), MODE_FULL_AUTO, 300, PRICES)
    assert out["order"]["status"] == STATUS_REJECTED
    assert "single-position cap" in out["order"]["reason"]
    assert get_positions(session) == {}
    assert get_account(session, 100_000.0).cash == 100_000.0
    assert settle_open(session, D1, PRICES) == []  # 没有任何已提交订单可撮合


def test_semi_auto_over_cap_rejected_at_creation(session):
    out = handle_decision(session, _decision(session), MODE_SEMI_AUTO, 300, PRICES)
    assert out["order"]["status"] == STATUS_REJECTED
    assert list_pending(session) == []


def test_duplicate_active_order_suppressed(session):
    handle_decision(session, _decision(session), MODE_SEMI_AUTO, 10, PRICES)
    out = handle_decision(session, _decision(session), MODE_SEMI_AUTO, 10, PRICES)
    assert out["order"] is None and "duplicate" in out["note"]
    assert len(list_pending(session)) == 1


def test_unknown_mode_creates_nothing(session):
    # fail-safe:未知模式绝不建单
    out = handle_decision(session, _decision(session), "yolo", 10, PRICES)
    assert out["order"] is None
    assert list_pending(session) == []


def test_approve_regates_with_fresh_params(session):
    # 红线:批准时刻重新过闸门——创建时合法,批准前收紧参数后必须拒绝
    out = handle_decision(session, _decision(session), MODE_SEMI_AUTO, 10, PRICES)
    update_risk_params(session, single_position_cap_pct=0.001)  # 上限收紧到 100 元
    approved = approve_order(session, out["order"]["id"], D, PRICES)
    assert approved["order"]["status"] == STATUS_REJECTED
    assert "rejected at approval" in approved["order"]["reason"]


def test_approve_then_settle_fills(session):
    out = handle_decision(session, _decision(session), MODE_SEMI_AUTO, 10, PRICES)
    approved = approve_order(session, out["order"]["id"], D, PRICES)
    assert approved["order"]["status"] == STATUS_SUBMITTED
    fills = settle_open(session, D1, {"AAPL": 100.0})
    assert len(fills) == 1 and fills[0]["shares"] == 10
    assert get_order(session, out["order"]["id"]).status == STATUS_FILLED


def test_reject_order(session):
    out = handle_decision(session, _decision(session), MODE_SEMI_AUTO, 10, PRICES)
    rejected = reject_order(session, out["order"]["id"], reason="不想买")
    assert rejected["order"]["status"] == STATUS_REJECTED
    assert rejected["order"]["reason"] == "不想买"
    assert list_pending(session) == []


def test_approve_nonpending_is_refused(session):
    out = handle_decision(session, _decision(session), MODE_FULL_AUTO, 10, PRICES)
    result = approve_order(session, out["order"]["id"], D, PRICES)
    assert result["order"]["status"] == STATUS_SUBMITTED  # 原样返回,不重复提交
    assert "not pending" in result["note"]
    assert order_to_dict(get_order(session, out["order"]["id"]))["status"] == STATUS_SUBMITTED
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/execution/test_order_manager.py -v`
Expected: FAIL(`ModuleNotFoundError: app.execution.order_manager`)

- [ ] **Step 3: 实现**

`backend/app/execution/order_manager.py`:

```python
"""订单生命周期 + 模式分流:全系统唯一的下单 choke point。

安全红线:
- mode 由 decision_service 从 DB 读出后传入,本模块绝不采信 payload;
- 任何订单(semi/full、创建/批准)必须先过 RiskGate,拒绝即不提交,且留审计单;
- 未知模式 fail-safe 不建单。
"""
import datetime as dt
import logging

from sqlalchemy.orm import Session

from app.execution.account_state import build_account_state
from app.execution.paper import PaperBroker
from app.risk.gate import RiskGate, params_from_row
from app.risk.rules import OrderRequest
from app.store.models import DecisionRow, OrderRow
from app.store.repos import order_repo
from app.store.repos.settings_repo import MODE_FULL_AUTO, MODE_SEMI_AUTO, get_app_settings

logger = logging.getLogger(__name__)

_gate = RiskGate()
_broker = PaperBroker()


def order_to_dict(row: OrderRow) -> dict:
    return {"id": row.id, "as_of": row.as_of.isoformat(), "symbol": row.symbol,
            "side": row.side, "shares": row.shares, "status": row.status,
            "mode": row.mode, "reason": row.reason, "decision_id": row.decision_id}


def _gate_check(session: Session, symbol: str, side: str, shares: int,
                as_of: dt.date, prices: dict):
    request = OrderRequest(symbol=symbol, side=side, shares=shares,
                           price=float(prices.get(symbol, 0.0)), as_of=as_of)
    account = build_account_state(session, as_of, prices)
    return _gate.check(request, account, params_from_row(get_app_settings(session)))


def handle_decision(session: Session, decision: DecisionRow, mode: str,
                    shares: int, prices: dict) -> dict:
    """semi_auto → 待确认队列;full_auto → 过闸门后直提 PaperBroker;其余不建单。"""
    if mode not in (MODE_SEMI_AUTO, MODE_FULL_AUTO):
        return {"order": None, "note": f"mode {mode!r} does not create orders"}
    as_of, symbol, side = decision.as_of, decision.symbol, decision.action
    if order_repo.has_active_order(session, as_of, symbol):
        logger.warning("duplicate order suppressed for %s on %s", symbol, as_of)
        return {"order": None,
                "note": f"duplicate protection: active order already exists "
                        f"for {symbol} on {as_of}"}
    check = _gate_check(session, symbol, side, shares, as_of, prices)
    if not check.allowed:
        row = order_repo.create_order(session, as_of, symbol, side, shares,
                                      order_repo.STATUS_REJECTED, mode,
                                      decision_id=decision.id, reason=check.reason)
        return {"order": order_to_dict(row), "note": "rejected by risk gate"}
    if mode == MODE_SEMI_AUTO:
        row = order_repo.create_order(session, as_of, symbol, side, shares,
                                      order_repo.STATUS_PENDING_CONFIRMATION, mode,
                                      decision_id=decision.id)
        return {"order": order_to_dict(row), "note": "queued for confirmation"}
    row = order_repo.create_order(session, as_of, symbol, side, shares,
                                  order_repo.STATUS_APPROVED, mode,
                                  decision_id=decision.id)
    row = _broker.submit(session, row)
    return {"order": order_to_dict(row), "note": "submitted to paper broker"}


def list_pending(session: Session) -> list:
    return [order_to_dict(r) for r in
            order_repo.get_orders_by_status(session, order_repo.STATUS_PENDING_CONFIRMATION)]


def approve_order(session: Session, order_id: int, as_of: dt.date, prices: dict) -> dict:
    """人工批准。批准时刻重新过闸门(不是只在创建时)——市场/参数可能已变。"""
    row = order_repo.get_order(session, order_id)
    if row is None or row.status != order_repo.STATUS_PENDING_CONFIRMATION:
        return {"order": order_to_dict(row) if row else None,
                "note": f"order {order_id} is not pending confirmation"}
    check = _gate_check(session, row.symbol, row.side, row.shares, as_of, prices)
    if not check.allowed:
        row = order_repo.update_status(session, order_id, order_repo.STATUS_REJECTED,
                                       reason=f"rejected at approval: {check.reason}")
        return {"order": order_to_dict(row), "note": "rejected by risk gate at approval"}
    row = order_repo.update_status(session, order_id, order_repo.STATUS_APPROVED)
    row = _broker.submit(session, row)
    return {"order": order_to_dict(row), "note": "approved and submitted"}


def reject_order(session: Session, order_id: int, reason: str = "rejected by user") -> dict:
    row = order_repo.get_order(session, order_id)
    if row is None or row.status != order_repo.STATUS_PENDING_CONFIRMATION:
        return {"order": order_to_dict(row) if row else None,
                "note": f"order {order_id} is not pending confirmation"}
    row = order_repo.update_status(session, order_id, order_repo.STATUS_REJECTED,
                                   reason=reason)
    return {"order": order_to_dict(row), "note": "rejected"}


def settle_open(session: Session, fill_date: dt.date, open_prices: dict) -> list:
    """下一交易时段开盘撮合(CLI/cron 触发)。"""
    return [
        {"order_id": f.order_id, "symbol": f.symbol, "side": f.side,
         "shares": f.shares, "price": round(f.price, 4),
         "fill_date": f.fill_date.isoformat()}
        for f in _broker.process_fills(session, fill_date, open_prices)
    ]
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/execution/ -v`
Expected: test_order_manager 10 passed(execution/ 共 25 passed)

- [ ] **Step 5: 提交**

```bash
cd /data1/common/haibotong/stock-agent
git add backend/app/execution/order_manager.py backend/tests/execution/test_order_manager.py
git commit -m "feat: order manager as single mode-routing choke point with re-gated approval (M3 task 11)"
```

---

### Task 12: decision_service:mode 改从 DB 读(payload 指定无效)+ shares 校验

**Files:**
- Modify: `backend/app/services/decision_service.py`
- Modify: `backend/tests/helpers.py`(`make_decision_payload` 增加 `shares`)
- Modify: `backend/tests/services/test_decision_service.py`(两个 mode 相关断言随语义更新)
- Test: `backend/tests/services/test_decision_service_m3.py`

**Interfaces:**
- Consumes: `get_mode/MODE_ADVISORY`(Task 3)、`handle_decision`(Task 11)、`save_decision`(M2)
- Produces(`app.services.decision_service`,对外名字不变):
  - `ACTIONS = ("buy", "sell", "hold")`(不变)、新增 `TRADE_ACTIONS = ("buy", "sell")`;`ROLE_KEYS` 不变;**删除本模块的 `MODE_ADVISORY` 常量**(canonical 版在 settings_repo,别处无人 import 此常量)
  - `validate_decision(payload) -> dict`——M2 规则全保留;新增:action ∈ TRADE_ACTIONS 时 `shares` 必须为正 int(bool 不算);**`normalized.pop("mode", None)`:mode 唯一真相在 DB,payload 里的 mode 一律剥掉**(安全红线)
  - `submit_decision(session: Session, payload, prices: dict | None = None) -> dict`——validate → `mode = get_mode(session)`(fail-safe advisory)→ `save_decision`(mode 从 DB 来)→ advisory 或 action=hold:commit 返回(M2 行为);否则 `handle_decision(session, row, mode, normalized["shares"], prices or {})` → commit → 结果并入返回 dict(`order`/`note` 键)。`prices` 由**服务端**(MCP 工具层/CLI)注入,payload 无通道
- 破坏性说明:`make_decision_payload` 增加 `"shares": 10` 后,M2 既有测试(payload 均经 helper 构造)全部继续通过;`validate_decision` 不再返回 `mode` 键 → 更新 test_decision_service.py 的两处断言(语义:从"强制 advisory"升级为"剥掉,DB 决定")

- [ ] **Step 1: 更新测试工具与既有断言(先红后绿的一部分)**

`backend/tests/helpers.py` 的 `make_decision_payload` 中,在 `"confidence": 0.8,` 一行后**新增一行**:

```python
        "shares": 10,
```

`backend/tests/services/test_decision_service.py` 中两个测试**整体替换**为:

```python
def test_validate_normalizes():
    out = validate_decision(make_decision_payload(symbol="aapl "))
    assert out["symbol"] == "AAPL"
    assert out["confidence"] == 0.8
    assert out["shares"] == 10
    assert "mode" not in out  # mode 唯一真相在 DB,校验层直接剥掉


def test_mode_cannot_be_forced_by_caller():
    out = validate_decision(make_decision_payload(mode="full_auto"))
    assert "mode" not in out  # 服务端不信任调用方;DB 分流见 test_decision_service_m3
```

- [ ] **Step 2: 写失败测试**

`backend/tests/services/test_decision_service_m3.py`:

```python
"""M3:mode 唯一真相在 DB;payload 指定 mode/旁路字段一律无效;按模式分流。"""
import pytest

from app.services.decision_service import (DecisionValidationError, submit_decision,
                                           validate_decision)
from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.order_repo import (STATUS_PENDING_CONFIRMATION, STATUS_REJECTED,
                                        STATUS_SUBMITTED, get_orders_by_status)
from app.store.repos.paper_repo import set_position
from app.store.repos.settings_repo import (MODE_FULL_AUTO, MODE_SEMI_AUTO,
                                           get_app_settings, set_mode)
from tests.helpers import make_decision_payload

PRICES = {"AAPL": 100.0}


@pytest.fixture
def session():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


def _all_orders(session):
    return [row for status in (STATUS_PENDING_CONFIRMATION, STATUS_SUBMITTED,
                               STATUS_REJECTED)
            for row in get_orders_by_status(session, status)]


def test_unset_mode_fail_safe_advisory_no_order(session):
    # 红线:未设 → advisory,不生成订单
    result = submit_decision(session, make_decision_payload(), prices=PRICES)
    assert result["status"] == "recorded" and result["mode"] == "advisory"
    assert _all_orders(session) == []


def test_payload_cannot_force_mode(session):
    # 红线:mode 只从 DB 读;payload 传 full_auto + 旁路字段一律无效
    payload = make_decision_payload(mode="full_auto", risk_override=True, skip_gate=True)
    result = submit_decision(session, payload, prices=PRICES)
    assert result["mode"] == "advisory"
    assert _all_orders(session) == []


def test_unknown_db_mode_fail_safe_advisory(session):
    # 红线:DB 值被写坏 → advisory
    get_app_settings(session).mode = "yolo"
    session.flush()
    result = submit_decision(session, make_decision_payload(), prices=PRICES)
    assert result["mode"] == "advisory"
    assert _all_orders(session) == []


def test_semi_auto_creates_pending_order(session):
    set_mode(session, MODE_SEMI_AUTO)
    result = submit_decision(session, make_decision_payload(), prices=PRICES)
    assert result["mode"] == MODE_SEMI_AUTO
    assert result["order"]["status"] == STATUS_PENDING_CONFIRMATION


def test_full_auto_within_caps_submits(session):
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    result = submit_decision(session, make_decision_payload(), prices=PRICES)
    assert result["order"]["status"] == STATUS_SUBMITTED


def test_full_auto_over_cap_rejected_even_with_bypass_keys(session):
    # 红线:gate 不可被 payload/工具参数绕过
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    payload = make_decision_payload(shares=300, skip_gate=True, risk_override="all")
    result = submit_decision(session, payload, prices=PRICES)
    assert result["order"]["status"] == STATUS_REJECTED
    assert "single-position cap" in result["order"]["reason"]


def test_full_auto_buy_without_price_fail_safe_rejected(session):
    # 服务端取不到参考价 → default-deny,而不是按 0 元放行
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    result = submit_decision(session, make_decision_payload(), prices={})
    assert result["order"]["status"] == STATUS_REJECTED


def test_stale_held_quote_blocks_buys_allows_sells(session):
    # 红线加固(finding #6):某持仓当前报价缺失 → 权益不可信 → full_auto 下
    # 任何标的的买单被拒(仅允许卖出)。删掉 StaleQuoteRule 或 account_state 的
    # stale 采集,此测试即 fail。
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    set_position(session, "AAPL", 10, 100.0)  # 持有 AAPL
    # 买 MSFT,但 prices 里没有持仓 AAPL 的报价 → AAPL stale → 买单一律拒
    buy = submit_decision(session, make_decision_payload(symbol="MSFT", shares=1),
                          prices={"MSFT": 50.0})
    assert buy["order"]["status"] == STATUS_REJECTED
    assert "报价缺失" in buy["order"]["reason"]
    # 卖出持仓仍放行(AAPL 报价依旧缺失)
    sell = submit_decision(session, make_decision_payload(symbol="AAPL", action="sell",
                                                         shares=5), prices={"MSFT": 50.0})
    assert sell["order"]["status"] == STATUS_SUBMITTED


def test_hold_never_creates_order(session):
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    payload = make_decision_payload(action="hold")
    del payload["shares"]  # hold 不要求 shares
    result = submit_decision(session, payload, prices=PRICES)
    assert result["status"] == "recorded"
    assert _all_orders(session) == []


def test_shares_required_for_trade_actions():
    payload = make_decision_payload()
    del payload["shares"]
    with pytest.raises(DecisionValidationError):
        validate_decision(payload)
    with pytest.raises(DecisionValidationError):
        validate_decision(make_decision_payload(shares=0))
    with pytest.raises(DecisionValidationError):
        validate_decision(make_decision_payload(shares=True))
```

- [ ] **Step 3: 运行确认失败**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/services/test_decision_service_m3.py tests/services/test_decision_service.py -v`
Expected: test_decision_service_m3 全 FAIL(旧实现:`validate_decision` 仍强制 mode=advisory、无 shares 校验、`submit_decision` 不接受 prices);test_decision_service 中新断言 `"mode" not in out` FAIL

- [ ] **Step 4: 实现**

`backend/app/services/decision_service.py` 全文替换为:

```python
"""委员会决定的服务端校验、落库与模式分流。

安全红线:
- schema 校验在服务端强制执行,LLM/调用方不可绕过;
- mode 的唯一真相在 DB settings row:payload 里的 mode 一律剥掉,
  未知/未设一律按 advisory 处理(fail-safe);
- 非 advisory 模式经 order_manager 单一 choke point 分流,任何订单必过风控闸门;
- prices 由服务端注入(MCP 工具层/CLI 取行情),payload 没有价格通道。
"""
import datetime as dt
import json

from sqlalchemy.orm import Session

from app.execution.order_manager import handle_decision
from app.store.repos.decision_repo import save_decision
from app.store.repos.settings_repo import MODE_ADVISORY, get_mode

ACTIONS = ("buy", "sell", "hold")
TRADE_ACTIONS = ("buy", "sell")
ROLE_KEYS = ("technical", "fundamental", "sentiment", "bear")


class DecisionValidationError(ValueError):
    """submit_decision 的 payload 不合规。"""


def _require(cond, msg: str) -> None:
    if not cond:
        raise DecisionValidationError(msg)


def _require_text(value, msg: str) -> None:
    _require(isinstance(value, str) and value.strip(), msg)


def validate_decision(payload) -> dict:
    """校验并归一化 payload;不合规抛 DecisionValidationError。mode 字段一律剥掉。"""
    _require(isinstance(payload, dict), "payload must be a dict")
    symbol = payload.get("symbol")
    _require_text(symbol, "symbol must be a non-empty string")
    try:
        as_of = dt.date.fromisoformat(str(payload.get("as_of")))
    except ValueError:
        raise DecisionValidationError("as_of must be an ISO date (YYYY-MM-DD)") from None
    _require(payload.get("action") in ACTIONS, f"action must be one of {ACTIONS}")
    confidence = payload.get("confidence")
    _require(isinstance(confidence, (int, float)) and not isinstance(confidence, bool)
             and 0.0 <= float(confidence) <= 1.0, "confidence must be a number in [0, 1]")
    if payload.get("action") in TRADE_ACTIONS:
        shares = payload.get("shares")
        _require(isinstance(shares, int) and not isinstance(shares, bool) and shares > 0,
                 "shares must be a positive integer for buy/sell decisions")
    committee = payload.get("committee")
    _require(isinstance(committee, dict), "committee must be a dict with four role sections")
    for role in ROLE_KEYS:
        section = committee.get(role)
        _require(isinstance(section, dict), f"committee.{role} section is required")
        _require_text(section.get("summary"), f"committee.{role}.summary must be non-empty")
    chair = payload.get("chair")
    _require(isinstance(chair, dict), "chair section is required")
    _require_text(chair.get("verdict"), "chair.verdict must be non-empty")
    _require_text(chair.get("bear_rebuttal"),
                  "chair.bear_rebuttal must be non-empty (裁决必须显式回应空头)")
    normalized = dict(payload)
    normalized.pop("mode", None)  # 安全红线:mode 唯一真相在 DB,不信任调用方
    normalized["symbol"] = symbol.strip().upper()
    normalized["as_of"] = as_of.isoformat()
    normalized["confidence"] = float(confidence)
    return normalized


def submit_decision(session: Session, payload, prices: dict | None = None) -> dict:
    """校验 → 从 DB 读 mode(唯一真相)→ 落库 → 按模式分流订单。"""
    normalized = validate_decision(payload)
    mode = get_mode(session)  # fail-safe:未知/未设 → advisory
    normalized["mode"] = mode
    row = save_decision(
        session,
        as_of=dt.date.fromisoformat(normalized["as_of"]),
        symbol=normalized["symbol"],
        action=normalized["action"],
        confidence=normalized["confidence"],
        mode=mode,
        payload_json=json.dumps(normalized, ensure_ascii=False),
    )
    result = {"status": "recorded", "id": row.id, "mode": mode, "symbol": row.symbol,
              "action": row.action, "as_of": normalized["as_of"]}
    if mode == MODE_ADVISORY or row.action == "hold":
        session.commit()
        result["note"] = "advisory/hold:已落库并将进入日报,不生成订单"
        return result
    routed = handle_decision(session, row, mode, normalized["shares"], prices or {})
    session.commit()
    result.update(routed)
    return result
```

- [ ] **Step 5: 运行确认通过**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/services/ tests/mcp/ -v`
Expected: test_decision_service_m3 10 passed;test_decision_service 全绿;tests/mcp/ 无回归(工具层 payload 均经 helper,已带 shares)

- [ ] **Step 6: 全量回归**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest`
Expected: 全部通过,0 failed(此刻约 235 passed, 3 deselected;含 stale-quote fail-safe 加固)

- [ ] **Step 7: 提交**

```bash
cd /data1/common/haibotong/stock-agent
git add backend/app/services/decision_service.py backend/tests/helpers.py backend/tests/services/test_decision_service.py backend/tests/services/test_decision_service_m3.py
git commit -m "feat: DB-driven mode routing in decision service with payload mode stripped (M3 task 12)"
```

---
### Task 13: MCP 面:get_pending_orders 只读工具 + 闸门价格注入 + as_of 全面 ET 化

**Files:**
- Modify: `backend/app/services/market_data_service.py`(追加三个取价函数)
- Create: `backend/app/mcp/tool_orders.py`
- Modify: `backend/app/mcp/tool_decision.py`(全文替换)
- Modify: `backend/app/mcp/server.py`(注册第五个工具)
- Modify: `backend/app/mcp/tool_screener.py`、`backend/app/mcp/tool_briefing.py`、`backend/app/cli.py`(as_of 由 host-local `dt.date.today()` 改为 ET 交易日)
- Modify: `backend/tests/mcp/test_server.py`(全文替换)、`backend/tests/mcp/test_tool_screener.py`、`backend/tests/mcp/test_e2e_advisory.py`(as_of 断言随 ET 化更新)
- Modify: `backend/tests/services/test_market_data_service.py`(追加)
- Modify: `backend/tests/mcp/test_tool_decision.py`(追加)
- Test: `backend/tests/mcp/test_tool_orders.py`

**Interfaces:**
- Produces(`app.services.market_data_service` 追加):
  - `latest_closes(bars_by_symbol: dict) -> dict`(每标的最后一根收盘价;空 DataFrame 跳过)
  - `latest_closes_for(provider: PriceProvider, symbols: list, as_of: dt.date, lookback_days: int = 14) -> dict`(服务端取闸门参考价:`fetch_bars` + `latest_closes`)
  - `open_prices_for(provider: PriceProvider, symbols: list, on_date: dt.date) -> dict`(撮合日开盘价:当日 bar 的 `open`)
- Produces(`app.mcp.tool_orders`):`get_pending_orders() -> dict`——**只读**列出待确认队列 `{"pending": [order_to_dict...]}`;**不存在**批准/拒绝的 MCP 工具(半自动人审红线,批准只能走 CLI/UI)
- Produces(`app.mcp.tool_decision`):`submit_decision(payload)` 签名不变;内部先 `get_mode`,非 advisory 才由**服务端**经 `latest_closes_for` 取价(标的 + 全部持仓)传给 service——payload 依旧没有价格/模式通道
- as_of 约定(M2 review backlog):`tool_screener`/`tool_briefing`/`cli.cmd_screen`/`cli.cmd_report` 的 as_of 一律 `et_trading_day(dt.datetime.now(dt.UTC))`;`data/cache.py` 的 `date.today()` 只影响缓存保守性,不动

- [ ] **Step 1: 写失败测试**

`backend/tests/mcp/test_tool_orders.py`:

```python
import datetime as dt

import pytest

import app.mcp.runtime as runtime
from app.mcp.tool_orders import get_pending_orders
from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.order_repo import (STATUS_PENDING_CONFIRMATION, STATUS_SUBMITTED,
                                        create_order)

D = dt.date(2026, 7, 17)


@pytest.fixture
def factory(monkeypatch):
    engine = make_engine(":memory:")
    init_db(engine)
    factory = make_session_factory(engine)
    monkeypatch.setattr(runtime, "open_session", lambda: factory())
    return factory


def test_empty_queue(factory):
    assert get_pending_orders() == {"pending": []}


def test_lists_pending_only(factory):
    with factory() as session:
        create_order(session, D, "AAPL", "buy", 10, STATUS_PENDING_CONFIRMATION, "semi_auto")
        create_order(session, D, "MSFT", "buy", 5, STATUS_SUBMITTED, "full_auto")
        session.commit()
    out = get_pending_orders()
    assert [o["symbol"] for o in out["pending"]] == ["AAPL"]
    assert out["pending"][0]["status"] == STATUS_PENDING_CONFIRMATION
```

`backend/tests/mcp/test_server.py` 全文替换为:

```python
import asyncio

from fastmcp import FastMCP

from app.mcp.server import build_server


def test_server_registers_all_tools():
    server = build_server()
    assert isinstance(server, FastMCP)
    tools = asyncio.run(server.list_tools())
    assert {"run_screener", "get_stock_briefing", "submit_decision",
            "run_backtest", "get_pending_orders"} == {t.name for t in tools}


def test_no_approval_or_fund_egress_tools_exposed():
    # 红线:不存在自动批准工具(人审是半自动的意义);不存在资金转出工具
    tools = asyncio.run(build_server().list_tools())
    names = " ".join(t.name.lower() for t in tools)
    for forbidden in ("approve", "confirm", "cancel", "transfer", "withdraw", "deposit"):
        assert forbidden not in names
```

`backend/tests/services/test_market_data_service.py` 末尾追加(顶部 import 区确保含:`import datetime as dt`、`import pytest`、`from app.data.base import PriceProvider, empty_bars`、`from app.services.market_data_service import fetch_bars, latest_closes, latest_closes_for, open_prices_for`、`from tests.helpers import make_bars`;已有的不重复):

```python
def test_latest_closes_skips_empty():
    bars = {"AAA": make_bars(days=5), "BBB": empty_bars()}
    out = latest_closes(bars)
    assert out["AAA"] == pytest.approx(104.0)  # base=100, step=1 → 最后收盘 104
    assert "BBB" not in out


def test_latest_closes_for_fetches_recent_window():
    class Anchored(PriceProvider):
        def get_daily_bars(self, symbol, start, end):
            return make_bars(start=(end - dt.timedelta(days=13)).isoformat(),
                             days=10, base=100.0)

    out = latest_closes_for(Anchored(), ["AAA"], dt.date(2026, 7, 20))
    assert out == {"AAA": pytest.approx(109.0)}


def test_open_prices_for_uses_the_days_open():
    class OneDay(PriceProvider):
        def get_daily_bars(self, symbol, start, end):
            return make_bars(start=start.isoformat(), days=1, base=200.0)

    out = open_prices_for(OneDay(), ["AAA"], dt.date(2026, 7, 20))
    assert out == {"AAA": pytest.approx(199.5)}  # open = close - 0.5
```

`backend/tests/mcp/test_tool_decision.py` 末尾追加(顶部 import 区补充:`import datetime as dt` 已有;新增 `from app.data.base import PriceProvider`、`from app.store.repos.settings_repo import MODE_SEMI_AUTO, set_mode`、`from tests.helpers import make_bars`):

```python
class AnchoredPrices(PriceProvider):
    def get_daily_bars(self, symbol, start, end):
        return make_bars(start=(end - dt.timedelta(days=13)).isoformat(), days=10, base=100.0)


def test_db_semi_auto_routes_via_tool(factory, monkeypatch):
    # mode 从 DB 读;闸门参考价由服务端 provider 取,payload 无价格通道
    monkeypatch.setattr(runtime, "get_price_provider", lambda: AnchoredPrices())
    with factory() as session:
        set_mode(session, MODE_SEMI_AUTO)
        session.commit()
    result = submit_decision(make_decision_payload())
    assert result["mode"] == "semi_auto"
    assert result["order"]["status"] == "pending_confirmation"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/mcp/test_tool_orders.py tests/mcp/test_server.py tests/services/test_market_data_service.py tests/mcp/test_tool_decision.py -v`
Expected: FAIL(`ModuleNotFoundError: app.mcp.tool_orders`;server 只有四个工具;market_data_service 无 latest_closes;tool_decision 不路由 semi_auto)

- [ ] **Step 3: 实现**

`backend/app/services/market_data_service.py` 末尾追加:

```python
def latest_closes(bars_by_symbol: dict) -> dict:
    """每标的最后一根日线的收盘价;空 DataFrame 跳过。"""
    out = {}
    for symbol, bars in bars_by_symbol.items():
        if bars is not None and not bars.empty:
            out[symbol] = float(bars["close"].iloc[-1])
    return out


def latest_closes_for(provider: PriceProvider, symbols: list, as_of: dt.date,
                      lookback_days: int = 14) -> dict:
    """服务端取闸门参考价(最新收盘)。调用方 payload 没有价格通道。"""
    bars, _skipped = fetch_bars(provider, symbols,
                                as_of - dt.timedelta(days=lookback_days), as_of)
    return latest_closes(bars)


def open_prices_for(provider: PriceProvider, symbols: list, on_date: dt.date) -> dict:
    """撮合日开盘价:取 on_date 当日 bar 的 open;当日无 bar 的标的缺席(由 broker 撤单留痕)。"""
    bars, _skipped = fetch_bars(provider, symbols, on_date, on_date)
    return {symbol: float(df["open"].iloc[-1]) for symbol, df in bars.items() if not df.empty}
```

`backend/app/mcp/tool_orders.py`:

```python
from app.execution.order_manager import list_pending
from app.mcp import runtime


def get_pending_orders() -> dict:
    """只读:列出待人工确认的订单队列(agent 可汇报,不可批准)。

    安全红线:批准/拒绝只能由人在 CLI/Web UI 完成——系统不提供自动批准的 MCP 工具。
    """
    with runtime.open_session() as session:
        return {"pending": list_pending(session)}
```

`backend/app/mcp/tool_decision.py` 全文替换为:

```python
import datetime as dt

from app.mcp import runtime
from app.services.decision_service import DecisionValidationError
from app.services.decision_service import submit_decision as _submit_decision
from app.services.market_data_service import latest_closes_for
from app.store.repos.paper_repo import get_positions
from app.store.repos.settings_repo import MODE_ADVISORY, get_mode
from app.util.trading_day import et_trading_day


def _gate_prices(session, payload) -> dict:
    """非 advisory 模式下为闸门取最新参考价(决策标的 + 全部持仓)。

    价格由服务端 provider 取,调用方 payload 没有价格通道(安全红线)。
    """
    if not isinstance(payload, dict) or get_mode(session) == MODE_ADVISORY:
        return {}
    symbols = {str(payload.get("symbol", "")).strip().upper()} | set(get_positions(session))
    symbols.discard("")
    if not symbols:
        return {}
    as_of = et_trading_day(dt.datetime.now(dt.UTC))
    return latest_closes_for(runtime.get_price_provider(), sorted(symbols), as_of)


def submit_decision(payload: dict) -> dict:
    """提交委员会结构化决定。mode 唯一真相在 DB settings;payload 传 mode 无效。

    校验失败返回 {"status": "rejected", "error": ...}(不抛异常,便于 agent 修正重试)。
    """
    with runtime.open_session() as session:
        try:
            return _submit_decision(session, payload, prices=_gate_prices(session, payload))
        except DecisionValidationError as exc:
            return {"status": "rejected", "error": str(exc)}
```

`backend/app/mcp/server.py`:import 区追加 `from app.mcp.tool_orders import get_pending_orders`,注册行改为:

```python
    for fn in (run_screener, get_stock_briefing, submit_decision, run_backtest,
               get_pending_orders):
        mcp.tool(fn)
```

as_of ET 化(四处精确替换,均需在该文件 import 区补 `from app.util.trading_day import et_trading_day`):

- `backend/app/mcp/tool_screener.py`:`as_of = dt.date.today()` → `as_of = et_trading_day(dt.datetime.now(dt.UTC))`
- `backend/app/mcp/tool_briefing.py`:`as_of=dt.date.today(),` → `as_of=et_trading_day(dt.datetime.now(dt.UTC)),`
- `backend/app/cli.py` `cmd_screen`:`as_of = dt.date.today()` → `as_of = et_trading_day(dt.datetime.now(dt.UTC))`
- `backend/app/cli.py` `cmd_report`:`report_date = args.date or dt.date.today()` → `report_date = args.date or et_trading_day(dt.datetime.now(dt.UTC))`

测试同步 ET 化(两个文件 import 区补 `from app.util.trading_day import et_trading_day`):

- `backend/tests/mcp/test_tool_screener.py`:`assert out["as_of"] == dt.date.today().isoformat()` → `assert out["as_of"] == et_trading_day(dt.datetime.now(dt.UTC)).isoformat()`;`rows = get_signals(session, dt.date.today())` → `rows = get_signals(session, et_trading_day(dt.datetime.now(dt.UTC)))`
- `backend/tests/mcp/test_e2e_advisory.py`:`today = dt.date.today()` → `today = et_trading_day(dt.datetime.now(dt.UTC))`

- [ ] **Step 4: 运行确认通过**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/mcp/ tests/services/ tests/test_cli.py -v`
Expected: test_tool_orders 2 passed、test_server 2 passed、test_tool_decision 3 passed、market_data 追加 3 passed;screener/e2e/cli 在 ET 语义下无回归

- [ ] **Step 5: 提交**

```bash
cd /data1/common/haibotong/stock-agent
git add backend/app/services/market_data_service.py backend/app/mcp backend/app/cli.py backend/tests/mcp backend/tests/services/test_market_data_service.py
git commit -m "feat: read-only pending-orders MCP tool, server-side gate prices, ET as_of everywhere (M3 task 13)"
```

---

### Task 14: watchdog:心跳/告警仓储 + monitor 纯函数 + 自动降级 advisory

**Files:**
- Create: `backend/app/store/repos/heartbeat_repo.py`
- Create: `backend/app/store/repos/alert_repo.py`
- Create: `backend/app/watchdog/__init__.py`(空)
- Create: `backend/app/watchdog/monitor.py`
- Create: `backend/tests/watchdog/__init__.py`(空)
- Modify: `backend/app/mcp/tool_screener.py`(记录成功/失败心跳)
- Modify: `backend/tests/mcp/test_tool_screener.py`(追加两条心跳测试)
- Test: `backend/tests/store/test_heartbeat_alert_repos.py`
- Test: `backend/tests/watchdog/test_monitor.py`

**Interfaces:**
- Produces(`app.store.repos.heartbeat_repo`):`record_heartbeat(session, job: str, ok: bool, ran_at: dt.datetime, detail: str = "") -> HeartbeatRow`;`recent_heartbeats(session, job: str, limit: int = 10) -> list`(ran_at 降序,新→旧)
- Produces(`app.store.repos.alert_repo`):`add_alert(session, kind: str, message: str) -> AlertRow`;`get_alerts(session, kind: str | None = None) -> list`(id 升序)
- Produces(`app.watchdog.monitor`):
  - 常量 `WATCHED_JOBS = ("premarket_screen",)`、`MAX_GAP_HOURS = 30.0`、`MAX_CONSECUTIVE_FAILURES = 2`
  - `Verdict(healthy: bool, reason: str)`(frozen dataclass)
  - `assess(heartbeats: list, job: str, now_utc: dt.datetime, max_gap_hours: float = MAX_GAP_HOURS, max_consecutive_failures: int = MAX_CONSECUTIVE_FAILURES) -> Verdict`——**纯函数,时间注入**;无心跳 / 最新心跳距 now 超时 / 连续失败达阈值 → unhealthy;heartbeats 为该 job 记录(新→旧,duck-typed `.ran_at`/`.ok`,naive-UTC)
  - `check_and_enforce(session: Session, now_utc: dt.datetime) -> dict`——任一 watched job 不健康且当前 mode ≠ advisory → `set_mode(advisory)` + `add_alert("watchdog_downgrade", ...)` + commit;返回 `{"healthy", "mode_before", "mode_after", "downgraded", "reasons"}`(设计 §4 双保险;降级到 advisory 比设计文中"降半自动"更保守,按红线执行)
- Produces(`app.mcp.tool_screener` 修改):常量 `JOB_PREMARKET = "premarket_screen"`;每次运行结束记录 ok 心跳,异常路径记录 fail 心跳后原样抛出

- [ ] **Step 1: 写失败测试**

`backend/tests/store/test_heartbeat_alert_repos.py`:

```python
import datetime as dt

import pytest

from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.alert_repo import add_alert, get_alerts
from app.store.repos.heartbeat_repo import record_heartbeat, recent_heartbeats


@pytest.fixture
def session():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


def test_record_and_recent_heartbeats_desc_and_filtered(session):
    a = record_heartbeat(session, "premarket_screen", ok=True,
                         ran_at=dt.datetime(2026, 7, 17, 12, 0))
    b = record_heartbeat(session, "premarket_screen", ok=False,
                         ran_at=dt.datetime(2026, 7, 17, 13, 0), detail="boom")
    record_heartbeat(session, "other_job", ok=True, ran_at=dt.datetime(2026, 7, 17, 14, 0))
    beats = recent_heartbeats(session, "premarket_screen")
    assert [x.id for x in beats] == [b.id, a.id]  # 新→旧
    assert beats[0].detail == "boom" and beats[0].ok is False


def test_recent_heartbeats_limit(session):
    for hour in range(5):
        record_heartbeat(session, "j", ok=True, ran_at=dt.datetime(2026, 7, 17, hour))
    assert len(recent_heartbeats(session, "j", limit=3)) == 3


def test_alerts_roundtrip(session):
    add_alert(session, "watchdog_downgrade", "mode full_auto -> advisory")
    add_alert(session, "other", "x")
    assert [a.kind for a in get_alerts(session)] == ["watchdog_downgrade", "other"]
    assert len(get_alerts(session, "other")) == 1
```

`backend/tests/watchdog/test_monitor.py`:

```python
import datetime as dt

import pytest

from app.store.db import init_db, make_engine, make_session_factory
from app.store.models import HeartbeatRow
from app.store.repos.alert_repo import get_alerts
from app.store.repos.heartbeat_repo import record_heartbeat
from app.store.repos.settings_repo import (MODE_FULL_AUTO, MODE_SEMI_AUTO, get_mode,
                                           set_mode)
from app.watchdog.monitor import Verdict, assess, check_and_enforce

NOW = dt.datetime(2026, 7, 17, 12, 0)  # naive-UTC,注入


def _hb(hours_ago: float, ok: bool = True):
    return HeartbeatRow(job="premarket_screen", ok=ok,
                        ran_at=NOW - dt.timedelta(hours=hours_ago))


def test_assess_no_heartbeats_unhealthy():
    assert assess([], "premarket_screen", NOW) == Verdict(
        False, "premarket_screen: no heartbeat recorded")


def test_assess_fresh_ok_healthy():
    assert assess([_hb(2.0)], "premarket_screen", NOW).healthy


def test_assess_stale_unhealthy():
    out = assess([_hb(31.0)], "premarket_screen", NOW)
    assert not out.healthy and "31.0h" in out.reason


def test_assess_consecutive_failures_unhealthy():
    out = assess([_hb(1.0, ok=False), _hb(2.0, ok=False), _hb(3.0)],
                 "premarket_screen", NOW)
    assert not out.healthy and "consecutive failures" in out.reason


def test_assess_single_failure_then_success_healthy():
    assert assess([_hb(1.0, ok=False), _hb(2.0)], "premarket_screen", NOW).healthy


@pytest.fixture
def session():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


def test_downgrades_full_auto_and_records_alert(session):
    # 红线:watchdog 检测异常自动降级 advisory + 记 alert
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    session.commit()
    out = check_and_enforce(session, NOW)  # 无任何心跳 → unhealthy
    assert out["downgraded"] is True and out["mode_after"] == "advisory"
    assert get_mode(session) == "advisory"
    alerts = get_alerts(session, "watchdog_downgrade")
    assert len(alerts) == 1 and "full_auto" in alerts[0].message


def test_semi_auto_also_downgrades(session):
    set_mode(session, MODE_SEMI_AUTO)
    session.commit()
    assert check_and_enforce(session, NOW)["mode_after"] == "advisory"


def test_healthy_heartbeat_keeps_mode(session):
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    record_heartbeat(session, "premarket_screen", ok=True,
                     ran_at=NOW - dt.timedelta(hours=1))
    session.commit()
    out = check_and_enforce(session, NOW)
    assert out["healthy"] is True and out["downgraded"] is False
    assert get_mode(session) == "full_auto"


def test_advisory_stays_without_alert(session):
    out = check_and_enforce(session, NOW)
    assert out["downgraded"] is False and out["mode_after"] == "advisory"
    assert get_alerts(session) == []
```

`backend/tests/mcp/test_tool_screener.py` 末尾追加(import 区补充 `from app.store.repos.heartbeat_repo import recent_heartbeats`;`pytest` 已有):

```python
def test_screener_records_success_heartbeat(factory):
    run_screener(top_n=1)
    with factory() as session:
        beats = recent_heartbeats(session, "premarket_screen")
    assert len(beats) == 1 and beats[0].ok is True


def test_screener_records_failure_heartbeat(factory, monkeypatch):
    import app.mcp.tool_screener as mod

    def boom(_path):
        raise RuntimeError("universe exploded")

    monkeypatch.setattr(mod, "load_universe", boom)
    with pytest.raises(RuntimeError):
        run_screener(top_n=1)
    with factory() as session:
        beats = recent_heartbeats(session, "premarket_screen")
    assert len(beats) == 1 and beats[0].ok is False
    assert "universe exploded" in beats[0].detail
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/store/test_heartbeat_alert_repos.py tests/watchdog/ tests/mcp/test_tool_screener.py -v`
Expected: FAIL(`ModuleNotFoundError: app.store.repos.heartbeat_repo` / `app.watchdog.monitor`;screener 不记心跳)

- [ ] **Step 3: 实现**

`backend/app/store/repos/heartbeat_repo.py`:

```python
import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.store.models import HeartbeatRow


def record_heartbeat(session: Session, job: str, ok: bool, ran_at: dt.datetime,
                     detail: str = "") -> HeartbeatRow:
    """记录一次 cron 心跳。ran_at 为 naive-UTC,由调用方注入(便于测试)。"""
    row = HeartbeatRow(job=job, ok=ok, ran_at=ran_at, detail=detail)
    session.add(row)
    session.flush()
    return row


def recent_heartbeats(session: Session, job: str, limit: int = 10) -> list:
    """该 job 最近的心跳,新→旧。"""
    stmt = (select(HeartbeatRow).where(HeartbeatRow.job == job)
            .order_by(HeartbeatRow.ran_at.desc(), HeartbeatRow.id.desc()).limit(limit))
    return list(session.scalars(stmt))
```

`backend/app/store/repos/alert_repo.py`:

```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.store.models import AlertRow


def add_alert(session: Session, kind: str, message: str) -> AlertRow:
    row = AlertRow(kind=kind, message=message)
    session.add(row)
    session.flush()
    return row


def get_alerts(session: Session, kind=None) -> list:
    stmt = select(AlertRow).order_by(AlertRow.id)
    if kind is not None:
        stmt = stmt.where(AlertRow.kind == kind)
    return list(session.scalars(stmt))
```

先创建空文件 `backend/app/watchdog/__init__.py` 与 `backend/tests/watchdog/__init__.py`。

`backend/app/watchdog/monitor.py`:

```python
"""watchdog:cron 心跳检查(设计 §4 双保险)。

安全红线:检测到 cron 未按时执行或连续失败 → 自动降级 advisory + 记 alert。
assess 为纯函数(时间与心跳记录注入),不依赖调度器——生产由
`python -m app.cli watchdog` 经系统 cron 触发,不引入 APScheduler。
"""
import datetime as dt
import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.store.repos.alert_repo import add_alert
from app.store.repos.heartbeat_repo import recent_heartbeats
from app.store.repos.settings_repo import MODE_ADVISORY, get_mode, set_mode

logger = logging.getLogger(__name__)

WATCHED_JOBS = ("premarket_screen",)
MAX_GAP_HOURS = 30.0  # 每日任务,>30h 视为漏跑
MAX_CONSECUTIVE_FAILURES = 2


@dataclass(frozen=True)
class Verdict:
    healthy: bool
    reason: str


def assess(heartbeats: list, job: str, now_utc: dt.datetime,
           max_gap_hours: float = MAX_GAP_HOURS,
           max_consecutive_failures: int = MAX_CONSECUTIVE_FAILURES) -> Verdict:
    """纯函数:heartbeats 为该 job 的记录(新→旧,naive-UTC)。"""
    if not heartbeats:
        return Verdict(False, f"{job}: no heartbeat recorded")
    gap_hours = (now_utc - heartbeats[0].ran_at).total_seconds() / 3600
    if gap_hours > max_gap_hours:
        return Verdict(False, f"{job}: last heartbeat {gap_hours:.1f}h ago "
                              f"(> {max_gap_hours}h)")
    failures = 0
    for beat in heartbeats:
        if beat.ok:
            break
        failures += 1
    if failures >= max_consecutive_failures:
        return Verdict(False, f"{job}: {failures} consecutive failures")
    return Verdict(True, f"{job}: ok")


def check_and_enforce(session: Session, now_utc: dt.datetime) -> dict:
    """任一 watched job 不健康且当前非 advisory → 自动降级 advisory + 记 alert。"""
    verdicts = [assess(recent_heartbeats(session, job), job, now_utc)
                for job in WATCHED_JOBS]
    unhealthy = [v for v in verdicts if not v.healthy]
    mode_before = get_mode(session)
    downgraded = False
    if unhealthy and mode_before != MODE_ADVISORY:
        set_mode(session, MODE_ADVISORY)
        message = (f"mode {mode_before} -> advisory: "
                   + "; ".join(v.reason for v in unhealthy))
        add_alert(session, "watchdog_downgrade", message)
        logger.warning("watchdog downgraded: %s", message)
        downgraded = True
    session.commit()
    return {"healthy": not unhealthy, "mode_before": mode_before,
            "mode_after": get_mode(session), "downgraded": downgraded,
            "reasons": [v.reason for v in verdicts]}
```

`backend/app/mcp/tool_screener.py` 全文替换为(Task 13 的 ET 化已含):

```python
import datetime as dt

from app.config import get_settings
from app.mcp import runtime
from app.screener.universe import load_universe
from app.services.analysis_service import run_screen_on_bars
from app.services.market_data_service import fetch_bars
from app.store.repos.heartbeat_repo import record_heartbeat
from app.store.repos.signal_repo import save_signals
from app.util.trading_day import et_trading_day

JOB_PREMARKET = "premarket_screen"


def _heartbeat(ok: bool, detail: str = "") -> None:
    with runtime.open_session() as session:
        record_heartbeat(session, JOB_PREMARKET, ok=ok,
                         ran_at=dt.datetime.now(dt.UTC).replace(tzinfo=None),
                         detail=detail)
        session.commit()


def run_screener(top_n: int = 10) -> dict:
    """盘前筛选:对默认股票池打分排序,取 top_n,并把快照落库 signals 表。

    返回 results(降序:rank/symbol/total/parts)与 skipped(抓取失败的标的)。
    每次运行记录 watchdog 心跳(成功/失败),供自动降级检查。
    """
    if top_n < 1:
        return {"status": "error", "error": "top_n must be >= 1"}
    try:
        settings = get_settings()
        as_of = et_trading_day(dt.datetime.now(dt.UTC))
        start = as_of - dt.timedelta(days=settings.lookback_days)
        bars, skipped = fetch_bars(runtime.get_price_provider(), load_universe(None),
                                   start, as_of)
        scores = run_screen_on_bars(bars, top_n)
        with runtime.open_session() as session:
            save_signals(session, as_of, scores)
            session.commit()
    except Exception as exc:
        _heartbeat(False, str(exc)[:200])
        raise
    _heartbeat(True)
    return {
        "as_of": as_of.isoformat(),
        "results": [
            {
                "rank": rank,
                "symbol": s.symbol,
                "total": round(s.total, 4),
                "parts": {name: {"score": round(r.score, 4), "detail": r.detail}
                          for name, r in s.parts.items()},
            }
            for rank, s in enumerate(scores, 1)
        ],
        "skipped": [{"symbol": sym, "reason": reason} for sym, reason in skipped],
    }
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/store/test_heartbeat_alert_repos.py tests/watchdog/ tests/mcp/test_tool_screener.py -v`
Expected: 3 + 9 + 6 passed(screener 原 4 条 + 新 2 条)

- [ ] **Step 5: 提交**

```bash
cd /data1/common/haibotong/stock-agent
git add backend/app/store/repos/heartbeat_repo.py backend/app/store/repos/alert_repo.py backend/app/watchdog backend/app/mcp/tool_screener.py backend/tests/watchdog backend/tests/store/test_heartbeat_alert_repos.py backend/tests/mcp/test_tool_screener.py
git commit -m "feat: watchdog heartbeat monitor with auto-downgrade to advisory (M3 task 14)"
```

---

### Task 15: CLI orders/mode/watchdog + 全链路 e2e 闭环 + README/收尾

**Files:**
- Create: `backend/app/cli_trading.py`
- Modify: `backend/app/cli.py`(注册 M3 子命令 + func 分发)
- Modify: `backend/README.md`(追加 M3 用法)
- Test: `backend/tests/test_cli_trading.py`
- Test: `backend/tests/execution/test_e2e_paper_loop.py`

**Interfaces:**
- Consumes: Task 11 全部、`latest_closes_for/open_prices_for`(Task 13)、`check_and_enforce`(Task 14)、`MODES/get_mode/set_mode`(Task 3)、`STATUS_SUBMITTED/get_order/get_orders_by_status`(Task 4)、`get_positions`(Task 5)、`et_trading_day`(Task 1)
- Produces(`app.cli_trading`,薄壳):
  - `open_cli_session()`(settings.db_path → engine → session;测试 monkeypatch 此函数)、`_default_provider()`(测试 monkeypatch)
  - `register(sub) -> None`——注册三个子命令并 `set_defaults(func=...)`:
    - `orders {list|approve|reject|settle} [order_id] [--date]`(approve 用 `latest_closes_for` 取闸门价;settle 用 `open_prices_for` 取撮合价;approve/reject 缺 order_id 返回 2)
    - `mode {show|set} [value] [--confirm-full-auto]`(set full_auto 未带 flag → 打印错误返回 2,**不改 DB**)
    - `watchdog`(打印 JSON;unhealthy 退出码 1)
  - `cmd_orders(args, provider=None) -> int`、`cmd_mode(args) -> int`、`cmd_watchdog(args) -> int`
- Produces(`app.cli` 修改):`build_parser` 末尾调用 `register_trading(sub)`;`main` 对 screen/backtest/report 之外的命令走 `args.func(args)`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_cli_trading.py`:

```python
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


def test_watchdog_reports_and_downgrades(factory, capsys):
    with factory() as session:
        set_mode(session, MODE_SEMI_AUTO)
        session.commit()
    assert main(["watchdog"]) == 1  # 无心跳 → unhealthy → 退出码 1
    out = json.loads(capsys.readouterr().out)
    assert out["downgraded"] is True and out["mode_after"] == "advisory"
```

`backend/tests/execution/test_e2e_paper_loop.py`:

```python
"""M3 全链路:半自动与全自动在模拟盘上的闭环(fake 注入,离线)。"""
import datetime as dt

import pytest

from app.execution.order_manager import approve_order, list_pending, settle_open
from app.services.decision_service import submit_decision
from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.order_repo import (STATUS_FILLED, STATUS_PENDING_CONFIRMATION,
                                        STATUS_REJECTED, STATUS_SUBMITTED, get_order)
from app.store.repos.paper_repo import get_account, get_positions
from app.store.repos.settings_repo import MODE_FULL_AUTO, MODE_SEMI_AUTO, set_mode
from tests.helpers import make_decision_payload

D = dt.date(2026, 7, 17)
D1 = dt.date(2026, 7, 20)


@pytest.fixture
def session():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


def test_semi_auto_closed_loop(session):
    # 决定 → 待确认 → 人工批准(重过闸)→ 提交 → 次一交易时段开盘成交
    set_mode(session, MODE_SEMI_AUTO)
    result = submit_decision(session, make_decision_payload(), prices={"AAPL": 100.0})
    assert result["order"]["status"] == STATUS_PENDING_CONFIRMATION
    assert [o["id"] for o in list_pending(session)] == [result["order"]["id"]]
    approved = approve_order(session, result["order"]["id"], D, {"AAPL": 100.0})
    assert approved["order"]["status"] == STATUS_SUBMITTED
    fills = settle_open(session, D1, {"AAPL": 101.0})
    session.commit()
    assert len(fills) == 1 and fills[0]["shares"] == 10
    assert get_order(session, result["order"]["id"]).status == STATUS_FILLED
    assert get_positions(session)["AAPL"].shares == 10
    assert get_account(session, 100_000.0).cash < 100_000.0


def test_full_auto_closed_loop(session):
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    result = submit_decision(session, make_decision_payload(), prices={"AAPL": 100.0})
    assert result["order"]["status"] == STATUS_SUBMITTED
    fills = settle_open(session, D1, {"AAPL": 100.0})
    session.commit()
    assert len(fills) == 1
    assert get_positions(session)["AAPL"].shares == 10


def test_breaker_day_full_auto_only_sells(session):
    # 红线集成:日内权益回撤 >= 5% 触发熔断后,full_auto 当日买单全拒、卖单放行
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    submit_decision(session, make_decision_payload(shares=150), prices={"AAPL": 100.0})
    settle_open(session, D1, {"AAPL": 100.0})  # 持仓 150 股,成本约 100.05
    # D1 第一次评估(正常价):day_start 快照约 99_992.5
    first = submit_decision(session, make_decision_payload(
        symbol="MSFT", as_of="2026-07-20", shares=1),
        prices={"AAPL": 100.0, "MSFT": 10.0})
    assert first["order"]["status"] == STATUS_SUBMITTED
    # AAPL 暴跌至 50:权益 ~92_492,回撤 ~7.5% → 熔断,买单拒
    crashed = submit_decision(session, make_decision_payload(
        symbol="NVDA", as_of="2026-07-20", shares=1),
        prices={"AAPL": 50.0, "NVDA": 10.0})
    assert crashed["order"]["status"] == STATUS_REJECTED
    assert "circuit breaker" in crashed["order"]["reason"]
    assert get_account(session, 100_000.0).breaker_tripped_on == D1
    # 熔断日卖出放行
    sell = submit_decision(session, make_decision_payload(
        action="sell", as_of="2026-07-20", shares=150),
        prices={"AAPL": 50.0})
    assert sell["order"]["status"] == STATUS_SUBMITTED
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/test_cli_trading.py tests/execution/test_e2e_paper_loop.py -v`
Expected: test_cli_trading 全 FAIL(`ModuleNotFoundError: app.cli_trading`);e2e 应当直接通过(Task 12 已打通)——若 e2e 有 FAIL,先修分流实现再继续

- [ ] **Step 3: 实现**

`backend/app/cli_trading.py`:

```python
"""M3 交易 CLI 薄壳:orders(list/approve/reject/settle)、mode(show/set)、watchdog。

业务全部在 execution/order_manager 与 watchdog/monitor;这里只做参数解析与装配。
"""
import datetime as dt
import json

from app.config import get_settings
from app.data.cache import CachedPriceProvider
from app.data.prices_yfinance import YFinancePriceProvider
from app.execution.order_manager import (approve_order, list_pending, reject_order,
                                         settle_open)
from app.services.market_data_service import latest_closes_for, open_prices_for
from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.order_repo import (STATUS_SUBMITTED, get_order,
                                        get_orders_by_status)
from app.store.repos.paper_repo import get_positions
from app.store.repos.settings_repo import MODES, get_mode, set_mode
from app.util.trading_day import et_trading_day
from app.watchdog.monitor import check_and_enforce


def open_cli_session():
    settings = get_settings()
    engine = make_engine(settings.db_path)
    init_db(engine)
    return make_session_factory(engine)()


def _default_provider():
    return CachedPriceProvider(YFinancePriceProvider(), get_settings().cache_dir)


def register(sub) -> None:
    orders = sub.add_parser("orders", help="订单队列:list/approve/reject/settle")
    orders.add_argument("action", choices=["list", "approve", "reject", "settle"])
    orders.add_argument("order_id", nargs="?", type=int, default=None)
    orders.add_argument("--date", type=dt.date.fromisoformat, default=None,
                        help="settle 撮合日/approve 评估日(缺省=今天 ET)")
    orders.set_defaults(func=cmd_orders)

    mode = sub.add_parser("mode", help="查看/切换运行模式(唯一真相在 DB settings)")
    mode.add_argument("action", choices=["show", "set"])
    mode.add_argument("value", nargs="?", choices=list(MODES), default=None)
    mode.add_argument("--confirm-full-auto", action="store_true",
                      help="开启 full_auto 必须显式加此参数(安全红线)")
    mode.set_defaults(func=cmd_mode)

    wd = sub.add_parser("watchdog", help="cron 心跳检查;异常自动降级 advisory")
    wd.set_defaults(func=cmd_watchdog)


def cmd_orders(args, provider=None) -> int:
    provider = provider or _default_provider()
    as_of = args.date or et_trading_day(dt.datetime.now(dt.UTC))
    with open_cli_session() as session:
        if args.action == "list":
            rows = list_pending(session)
            if not rows:
                print("(no pending orders)")
            for row in rows:
                print(f"#{row['id']} {row['as_of']} {row['side']} {row['symbol']} "
                      f"x{row['shares']} [{row['status']}]")
            return 0
        if args.action == "settle":
            symbols = sorted({o.symbol for o in
                              get_orders_by_status(session, STATUS_SUBMITTED)})
            open_prices = open_prices_for(provider, symbols, as_of) if symbols else {}
            fills = settle_open(session, as_of, open_prices)
            session.commit()
            print(f"{len(fills)} fill(s)")
            for fill in fills:
                print(f"  {fill['fill_date']} {fill['side']} {fill['symbol']} "
                      f"x{fill['shares']} @ {fill['price']}")
            return 0
        if args.order_id is None:
            print("[error] approve/reject 需要 order_id")
            return 2
        if args.action == "approve":
            order = get_order(session, args.order_id)
            symbols = sorted(({order.symbol} if order else set())
                             | set(get_positions(session)))
            prices = latest_closes_for(provider, symbols, as_of) if symbols else {}
            result = approve_order(session, args.order_id, as_of, prices)
        else:
            result = reject_order(session, args.order_id)
        session.commit()
        print(result["note"])
        if result["order"]:
            print(result["order"])
        return 0


def cmd_mode(args) -> int:
    with open_cli_session() as session:
        if args.action == "show":
            print(f"mode: {get_mode(session)}")
            session.commit()
            return 0
        if args.value is None:
            print("[error] mode set 需要一个值(advisory/semi_auto/full_auto)")
            return 2
        try:
            set_mode(session, args.value, confirm_full_auto=args.confirm_full_auto)
        except ValueError as exc:
            print(f"[error] {exc}")
            return 2
        session.commit()
        print(f"mode set to {args.value}")
        return 0


def cmd_watchdog(args) -> int:
    with open_cli_session() as session:
        result = check_and_enforce(session, dt.datetime.now(dt.UTC).replace(tzinfo=None))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["healthy"] else 1
```

`backend/app/cli.py` 两处修改:

import 区追加一行:

```python
from app.cli_trading import register as register_trading
```

`build_parser` 中 `return parser` 之前插入:

```python
    register_trading(sub)
```

`main` 函数整体替换为:

```python
def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "screen":
        return cmd_screen(args)
    if args.command == "backtest":
        return cmd_backtest(args)
    if args.command == "report":
        return cmd_report(args)
    return args.func(args)  # M3 子命令(orders/mode/watchdog)经 set_defaults 分发
```

`backend/README.md` 末尾追加:

```markdown
## M3:模拟盘交易与风控

- 模式开关(唯一真相在 DB settings 表):
  `python -m app.cli mode show` / `python -m app.cli mode set semi_auto` /
  `python -m app.cli mode set full_auto --confirm-full-auto`(全自动必须显式确认)
- 订单队列(半自动人审):`python -m app.cli orders list` →
  `python -m app.cli orders approve <id>`(批准时重过风控闸门)/ `orders reject <id>`
- 开盘撮合(每交易日开盘后跑一次):`python -m app.cli orders settle`
- watchdog(建议系统 cron 每小时):`python -m app.cli watchdog`
  ——cron 心跳异常自动降级 advisory 并写 alerts 表
- 风控参数在 settings 表(单票/总仓位上限、单日新开仓数、日亏损熔断、冷却期、初始资金),
  M4 设置页开放修改;熔断触发后当日只允许卖出,重启不重置
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/test_cli_trading.py tests/execution/test_e2e_paper_loop.py tests/test_cli.py -v`
Expected: 7 + 3 passed,test_cli 无回归

- [ ] **Step 5: 全量回归 + 行数纪律检查**

```bash
cd /data1/common/haibotong/stock-agent/backend
.venv/bin/pytest
find app -name "*.py" | xargs wc -l | sort -n | tail -5
```

Expected: 全部通过 0 failed(约 266 passed, 3 deselected;以实际输出为准);`app/` 下所有文件 < 200 行(最大约 order_manager/paper 在 110-140 行)

- [ ] **Step 6: 提交**

```bash
cd /data1/common/haibotong/stock-agent
git add backend/app/cli_trading.py backend/app/cli.py backend/README.md backend/tests/test_cli_trading.py backend/tests/execution/test_e2e_paper_loop.py
git commit -m "feat: orders/mode/watchdog CLI and paper-trading e2e closed loop (M3 task 15)"
```

---

## 验收标准(M3 完成定义)

1. `cd backend && .venv/bin/pytest` 全绿(离线;M1/M2 149 条无回归 + M3 新增约 117 条;3 条 network deselected;socket 屏障兜底,任何单测意外联网即红)
2. **半自动闭环**(e2e 覆盖):DB mode=semi_auto → `submit_decision` 建 pending_confirmation 订单 → `orders list` 可见 → `orders approve <id>` 批准时刻重过闸门 → submitted → `orders settle` 次一交易时段开盘价成交(滑点、现金截断)→ filled,持仓/现金/成交流水落库
3. **全自动闭环**(e2e 覆盖):DB mode=full_auto(显式 confirm 才能开启)→ `submit_decision` 过闸门直提 PaperBroker → settle 成交;超限订单被拒并落 rejected 审计单,**绝不触达 broker**
4. **安全红线逐条有"删了就红"的守卫测试**:
   - 闸门不可绕过:`test_full_auto_over_cap_rejected_not_submitted`、`test_full_auto_over_cap_rejected_even_with_bypass_keys`(payload 带 skip_gate/risk_override 无效)、`test_default_deny_*`(非法输入默认拒)
   - 资金无出口:`test_no_fund_egress.py` 扫描全部 app 模块函数名 + `test_no_approval_or_fund_egress_tools_exposed`(MCP 工具面无 approve/transfer/withdraw 类工具)
   - mode 唯一真相在 DB + fail-safe:`test_payload_cannot_force_mode`、`test_unset_mode_fail_safe_advisory_no_order`、`test_unknown_db_mode_fail_safe_advisory`
   - 熔断只许卖 + 持久化:`test_circuit_breaker_blocks_buys_allows_sells`、`test_tripped_state_survives_restart_same_day`、`test_breaker_day_full_auto_only_sells`
   - stale-held-quote fail-safe(finding #6,熔断风险模型加固):持仓报价缺失 → 权益不可信 → 买单一律拒、仅允许卖出——`test_stale_quote_rule_blocks_buys_allows_sells`(规则单测)、`test_stale_held_quote_blocks_buys_allows_sells`(full_auto 集成);删掉 `StaleQuoteRule` 或 account_state 的 stale 采集即 fail
   - 全自动显式开启 + watchdog 降级:`test_full_auto_requires_explicit_confirm`(repo 层与 CLI 层各一)、`test_downgrades_full_auto_and_records_alert`
5. 半自动确认只有人可做:MCP 面仅新增只读 `get_pending_orders`;批准/拒绝仅存在于 CLI(M4 加 Web UI 入口)
6. 每次拒绝可审计:闸门拒绝落 rejected 订单行(reason=规则原因)+ logger.warning;watchdog 降级写 alerts 表
7. as_of 全面 ET 交易日(screener/briefing/report/orders);orders 有 (as_of, symbol) 活跃单重复保护
8. `app/` 下所有单文件 < 200 行;mcp/cli 薄壳,业务在 risk/execution/services/store;依赖零新增

## M4 预告(另出计划)

Web UI 全量页面(Next.js + TypeScript + Tailwind + lightweight-charts):dashboard(净值曲线/持仓/最近成交)、signals(每日快照 + briefing 展示)、orders(待确认队列的确认/拒绝——直接复用 `approve_order`/`reject_order` 的 REST 面,二次确认交互)、backtest(参数表单 + 净值图)、settings(模式开关含 full_auto 二次确认 + 风控参数编辑)。后端补 FastAPI REST 路由层(`api/routes_*.py` 薄壳,复用 services/execution,零业务逻辑)。之后按需接富途 moomoo OpenAPI 适配器(实现同一 `Broker` 抽象,`paper.py` 与实盘可切换)。
