# M1 量化底座 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭好交易系统的量化底座:数据层(yfinance + 缓存 + 回放)、量化筛选器(趋势/动量/量能)、quant-only 日线回测引擎、CLI 报告。不涉及 OpenClaw、LLM、Web UI。

**Architecture:** 纯 Python 包 `app`(位于 `backend/`),分层为 data → screener → backtest → services → report → cli;实盘与回测通过同一 `PriceProvider` 抽象切换;回测用 `ReplayPriceProvider` 杜绝未来函数,订单 T 日决策、T+1 开盘成交。

**Tech Stack:** Python 3.12(uv 管理)、pandas、numpy、yfinance、pydantic-settings、pyarrow、pytest。

**设计文档:** `docs/superpowers/specs/2026-07-17-stock-agent-design.md`

## Global Constraints

- 仓库根:`/data1/common/haibotong/stock-agent`;所有后端代码在 `backend/` 下,包名 `app`
- **单文件不超过约 200 行**;`cli` 是薄壳,业务逻辑放 `services/` 与领域模块(用户明确要求细粒度拆分)
- Python `>=3.12`;venv 用 `uv venv --python 3.12`(uv 在 `~/.local/bin/uv`,已有本地 cpython-3.12.13)
- 依赖白名单:pandas、numpy、yfinance、pydantic-settings、pyarrow、pytest,不引入其他库
- **单元测试一律离线**(合成数据 / monkeypatch);联网测试必须标 `@pytest.mark.network`,默认跳过
- 每个任务走 TDD:先写失败测试 → 实现 → 通过 → 提交;提交信息用 conventional commits(feat:/test:/chore:)
- pytest 统一从 `backend/` 目录运行:`cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest ...`
- 若 pip 源慢:`export UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple`

---

### Task 1: 项目脚手架 + config

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/.gitignore`
- Create: `backend/app/__init__.py`(空)
- Create: `backend/app/config.py`
- Create: `backend/tests/__init__.py`(空;之后每个 tests 子目录也要空 `__init__.py`,保证 `from tests.helpers import ...` 可导入)
- Create: `backend/tests/test_config.py`
- Create: `backend/app/{data,screener,backtest,services,report}/__init__.py`(全部空文件)

**Interfaces:**
- Produces: `app.config.Settings`(字段 `cache_dir: Path`、`reports_dir: Path`、`top_n: int`、`lookback_days: int`)、`get_settings() -> Settings`;环境变量前缀 `STOCKAGENT_`

- [ ] **Step 1: 写 pyproject 与 .gitignore**

`backend/pyproject.toml`:

```toml
[project]
name = "stock-agent-backend"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "pandas>=2.0",
    "numpy>=1.26",
    "yfinance>=0.2.40",
    "pydantic-settings>=2.0",
    "pyarrow>=14.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-m 'not network'"
markers = ["network: 需要联网的测试,默认跳过,用 pytest -m network 运行"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["app*"]
```

`backend/.gitignore`:

```
.venv/
__pycache__/
*.pyc
data_cache/
reports/
*.parquet
.pytest_cache/
*.egg-info/
```

- [ ] **Step 2: 建 venv 并安装**

```bash
cd /data1/common/haibotong/stock-agent/backend
~/.local/bin/uv venv --python 3.12 .venv
~/.local/bin/uv pip install --python .venv/bin/python -e ".[dev]"
```

Expected: 安装成功,`.venv/bin/pytest --version` 可运行。

- [ ] **Step 3: 写失败测试**

`backend/tests/test_config.py`:

```python
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
```

- [ ] **Step 4: 运行确认失败**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/test_config.py -v`
Expected: FAIL(`ModuleNotFoundError: app.config`)

- [ ] **Step 5: 实现**

先创建空包文件:`backend/app/__init__.py` 及 `backend/app/{data,screener,backtest,services,report}/__init__.py`(内容全部为空)。

`backend/app/config.py`:

```python
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
```

- [ ] **Step 6: 运行确认通过**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/test_config.py -v`
Expected: 3 passed

- [ ] **Step 7: 提交**

```bash
cd /data1/common/haibotong/stock-agent
git add backend
git commit -m "chore: backend scaffolding with config (M1 task 1)"
```

---

### Task 2: 测试数据工具 + 技术指标纯函数

**Files:**
- Create: `backend/tests/helpers.py`
- Create: `backend/tests/screener/__init__.py`(空)
- Create: `backend/app/screener/indicators.py`
- Test: `backend/tests/screener/test_indicators.py`

**Interfaces:**
- Produces(tests 侧): `make_bars(start="2024-01-01", days=10, base=100.0, step=1.0, volume=1_000_000) -> pd.DataFrame`——合成日线,bdate 索引,列 open/high/low/close/volume,`close = base + step*i`,`open = close - 0.5`
- Produces(app 侧): `sma(close, window)`、`ema(close, window)`、`rsi(close, window=14)`、`true_range(bars)`、`atr(bars, window=14)`、`pct_return(close, periods)`,全部 `pd.Series -> pd.Series` 纯函数(atr/true_range 输入为 DataFrame)

- [ ] **Step 1: 写测试工具**

`backend/tests/helpers.py`:

```python
import numpy as np
import pandas as pd


def make_bars(start="2024-01-01", days=10, base=100.0, step=1.0, volume=1_000_000):
    """构造合成日线:close = base + step*i,open=close-0.5,high/low=close±1。"""
    idx = pd.bdate_range(start, periods=days)
    close = pd.Series(base + step * np.arange(days, dtype=float), index=idx)
    return pd.DataFrame(
        {
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": float(volume),
        },
        index=idx,
    )
```

- [ ] **Step 2: 写失败测试**

`backend/tests/screener/test_indicators.py`:

```python
import math

import pandas as pd
import pytest

from app.screener.indicators import atr, ema, pct_return, rsi, sma, true_range
from tests.helpers import make_bars


def test_sma_known_values():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    out = sma(s, 3)
    assert math.isnan(out.iloc[1])
    assert out.iloc[2] == pytest.approx(2.0)
    assert out.iloc[4] == pytest.approx(4.0)


def test_ema_warmup_nan_then_value():
    s = pd.Series([1.0] * 10)
    out = ema(s, 5)
    assert math.isnan(out.iloc[3])
    assert out.iloc[-1] == pytest.approx(1.0)


def test_rsi_all_gains_near_100():
    s = pd.Series(range(1, 40), dtype=float)
    assert rsi(s).iloc[-1] > 95


def test_rsi_all_losses_near_0():
    s = pd.Series(range(40, 1, -1), dtype=float)
    assert rsi(s).iloc[-1] < 5


def test_rsi_bounded_on_mixed_series():
    s = pd.Series([100 + (i % 5) - 2 for i in range(60)], dtype=float)
    tail = rsi(s).dropna()
    assert ((tail >= 0) & (tail <= 100)).all()


def test_true_range_and_atr_positive():
    bars = make_bars(days=30)
    assert (true_range(bars).dropna() > 0).all()
    assert atr(bars).dropna().iloc[-1] > 0


def test_pct_return():
    s = pd.Series([100.0, 110.0, 121.0])
    assert pct_return(s, 1).iloc[-1] == pytest.approx(0.1)
    assert pct_return(s, 2).iloc[-1] == pytest.approx(0.21)
```

- [ ] **Step 3: 运行确认失败**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/screener/test_indicators.py -v`
Expected: FAIL(`ModuleNotFoundError: app.screener.indicators`)

- [ ] **Step 4: 实现**

`backend/app/screener/indicators.py`:

```python
import pandas as pd


def sma(close: pd.Series, window: int) -> pd.Series:
    return close.rolling(window, min_periods=window).mean()


def ema(close: pd.Series, window: int) -> pd.Series:
    return close.ewm(span=window, adjust=False, min_periods=window).mean()


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """Wilder RSI。全涨→100,全跌→0,横盘(无涨无跌)→NaN。"""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def true_range(bars: pd.DataFrame) -> pd.Series:
    prev_close = bars["close"].shift(1)
    ranges = pd.concat(
        [
            bars["high"] - bars["low"],
            (bars["high"] - prev_close).abs(),
            (bars["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(bars: pd.DataFrame, window: int = 14) -> pd.Series:
    return true_range(bars).ewm(alpha=1 / window, adjust=False, min_periods=window).mean()


def pct_return(close: pd.Series, periods: int) -> pd.Series:
    return close.pct_change(periods)
```

- [ ] **Step 5: 运行确认通过**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/screener/test_indicators.py -v`
Expected: 7 passed

- [ ] **Step 6: 提交**

```bash
cd /data1/common/haibotong/stock-agent
git add backend/tests/helpers.py backend/tests/screener backend/app/screener/indicators.py
git commit -m "feat: technical indicator pure functions (M1 task 2)"
```

---

### Task 3: PriceProvider 抽象 + 回放数据源(防未来函数)

**Files:**
- Create: `backend/app/data/base.py`
- Create: `backend/app/data/replay.py`
- Create: `backend/tests/data/__init__.py`(空)
- Test: `backend/tests/data/test_replay.py`

**Interfaces:**
- Produces: `BAR_COLUMNS = ["open","high","low","close","volume"]`;`empty_bars() -> pd.DataFrame`;抽象类 `PriceProvider.get_daily_bars(symbol: str, start: dt.date, end: dt.date) -> pd.DataFrame`(闭区间、升序 DatetimeIndex、无时区)
- Produces: `ReplayPriceProvider(bars_by_symbol: dict[str, pd.DataFrame])`,方法 `set_as_of(as_of: dt.date)`;`get_daily_bars` 把 `end` 截断到 `as_of`;未 `set_as_of` 就读数据 → `RuntimeError`

- [ ] **Step 1: 写失败测试**

`backend/tests/data/test_replay.py`:

```python
import datetime as dt

import pytest

from app.data.base import BAR_COLUMNS, empty_bars
from app.data.replay import ReplayPriceProvider
from tests.helpers import make_bars


def test_empty_bars_shape():
    df = empty_bars()
    assert list(df.columns) == BAR_COLUMNS
    assert df.empty


def test_replay_never_returns_future_rows():
    bars = make_bars(start="2024-01-01", days=10)  # 2024-01-01 ~ 2024-01-12 工作日
    p = ReplayPriceProvider({"AAA": bars})
    p.set_as_of(dt.date(2024, 1, 5))
    out = p.get_daily_bars("AAA", dt.date(2024, 1, 1), dt.date(2024, 1, 12))
    assert out.index.max().date() <= dt.date(2024, 1, 5)
    assert len(out) == 5  # 1/1 ~ 1/5 共 5 个工作日


def test_replay_requires_as_of():
    p = ReplayPriceProvider({"AAA": make_bars()})
    with pytest.raises(RuntimeError):
        p.get_daily_bars("AAA", dt.date(2024, 1, 1), dt.date(2024, 1, 5))


def test_replay_unknown_symbol_returns_empty():
    p = ReplayPriceProvider({})
    p.set_as_of(dt.date(2024, 1, 5))
    assert p.get_daily_bars("NOPE", dt.date(2024, 1, 1), dt.date(2024, 1, 5)).empty
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/data/test_replay.py -v`
Expected: FAIL(module not found)

- [ ] **Step 3: 实现**

`backend/app/data/base.py`:

```python
import datetime as dt
from abc import ABC, abstractmethod

import pandas as pd

BAR_COLUMNS = ["open", "high", "low", "close", "volume"]


def empty_bars() -> pd.DataFrame:
    return pd.DataFrame(columns=BAR_COLUMNS, index=pd.DatetimeIndex([]))


class PriceProvider(ABC):
    """日线行情来源抽象。实盘与回放实现同一接口,上层无感知。"""

    @abstractmethod
    def get_daily_bars(self, symbol: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        """返回 [start, end] 闭区间日线:升序 DatetimeIndex(无时区),
        列为 open/high/low/close/volume。无数据时返回 empty_bars()。"""
```

`backend/app/data/replay.py`:

```python
import datetime as dt

from app.data.base import PriceProvider, empty_bars


class ReplayPriceProvider(PriceProvider):
    """回测数据源:只暴露 as_of 及以前的数据,杜绝未来函数。"""

    def __init__(self, bars_by_symbol: dict):
        self._bars = bars_by_symbol
        self._as_of = None

    def set_as_of(self, as_of: dt.date) -> None:
        self._as_of = as_of

    def get_daily_bars(self, symbol: str, start: dt.date, end: dt.date):
        if self._as_of is None:
            raise RuntimeError("set_as_of() must be called before reading data")
        end = min(end, self._as_of)
        df = self._bars.get(symbol)
        if df is None or df.empty:
            return empty_bars()
        mask = (df.index.date >= start) & (df.index.date <= end)
        return df.loc[mask]
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/data/test_replay.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
cd /data1/common/haibotong/stock-agent
git add backend/app/data backend/tests/data
git commit -m "feat: PriceProvider abstraction and replay provider (M1 task 3)"
```

---

### Task 4: yfinance 数据源

**Files:**
- Create: `backend/app/data/prices_yfinance.py`
- Test: `backend/tests/data/test_prices_yfinance.py`

**Interfaces:**
- Consumes: `PriceProvider`、`BAR_COLUMNS`、`empty_bars`(Task 3)
- Produces: `YFinancePriceProvider()`——列名小写化(兼容 MultiIndex 列)、索引去时区并 normalize、auto_adjust 复权

- [ ] **Step 1: 写失败测试**

`backend/tests/data/test_prices_yfinance.py`:

```python
import datetime as dt

import numpy as np
import pandas as pd

import app.data.prices_yfinance as mod
from app.data.base import BAR_COLUMNS


def _fake_raw(multiindex: bool) -> pd.DataFrame:
    idx = pd.date_range("2024-01-02", periods=3, tz="America/New_York")
    data = {c: np.arange(3, dtype=float) + i for i, c in enumerate(["Open", "High", "Low", "Close", "Volume"])}
    df = pd.DataFrame(data, index=idx)
    if multiindex:
        df.columns = pd.MultiIndex.from_product([["Open", "High", "Low", "Close", "Volume"], ["AAPL"]])
    return df


def test_normalizes_plain_columns(monkeypatch):
    monkeypatch.setattr(mod.yf, "download", lambda *a, **k: _fake_raw(False))
    df = mod.YFinancePriceProvider().get_daily_bars("AAPL", dt.date(2024, 1, 2), dt.date(2024, 1, 4))
    assert list(df.columns) == BAR_COLUMNS
    assert df.index.tz is None
    assert len(df) == 3


def test_normalizes_multiindex_columns(monkeypatch):
    monkeypatch.setattr(mod.yf, "download", lambda *a, **k: _fake_raw(True))
    df = mod.YFinancePriceProvider().get_daily_bars("AAPL", dt.date(2024, 1, 2), dt.date(2024, 1, 4))
    assert list(df.columns) == BAR_COLUMNS


def test_empty_download_returns_empty_bars(monkeypatch):
    monkeypatch.setattr(mod.yf, "download", lambda *a, **k: pd.DataFrame())
    df = mod.YFinancePriceProvider().get_daily_bars("AAPL", dt.date(2024, 1, 2), dt.date(2024, 1, 4))
    assert df.empty and list(df.columns) == BAR_COLUMNS


def test_end_date_is_inclusive(monkeypatch):
    captured = {}

    def fake_download(symbol, **kwargs):
        captured.update(kwargs, symbol=symbol)
        return pd.DataFrame()

    monkeypatch.setattr(mod.yf, "download", fake_download)
    mod.YFinancePriceProvider().get_daily_bars("AAPL", dt.date(2024, 1, 2), dt.date(2024, 1, 4))
    assert captured["end"] == "2024-01-05"  # yfinance end 开区间,+1 天保证闭区间语义
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/data/test_prices_yfinance.py -v`
Expected: FAIL(module not found)

- [ ] **Step 3: 实现**

`backend/app/data/prices_yfinance.py`:

```python
import datetime as dt

import pandas as pd
import yfinance as yf

from app.data.base import BAR_COLUMNS, PriceProvider, empty_bars


class YFinancePriceProvider(PriceProvider):
    """yfinance 日线(auto_adjust 复权)。列名与时区归一化到 BAR_COLUMNS 约定。"""

    def get_daily_bars(self, symbol: str, start: dt.date, end: dt.date):
        raw = yf.download(
            symbol,
            start=start.isoformat(),
            end=(end + dt.timedelta(days=1)).isoformat(),  # yfinance end 为开区间
            interval="1d",
            auto_adjust=True,
            progress=False,
        )
        if raw is None or raw.empty:
            return empty_bars()
        df = raw.copy()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [str(c[0]).lower() for c in df.columns]
        else:
            df.columns = [str(c).lower() for c in df.columns]
        df = df[BAR_COLUMNS]
        idx = pd.to_datetime(df.index)
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        df.index = idx.normalize()
        return df.sort_index()
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/data/test_prices_yfinance.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
cd /data1/common/haibotong/stock-agent
git add backend/app/data/prices_yfinance.py backend/tests/data/test_prices_yfinance.py
git commit -m "feat: yfinance price provider (M1 task 4)"
```

---

### Task 5: parquet 本地缓存

**Files:**
- Create: `backend/app/data/cache.py`
- Test: `backend/tests/data/test_cache.py`

**Interfaces:**
- Consumes: `PriceProvider`(Task 3)
- Produces: `CachedPriceProvider(inner: PriceProvider, cache_dir: Path)`——命中范围直接读 parquet 切片;未命中回源、合并、写回。注意:覆盖判断基于缓存首末日期,`end` 落在非交易日会保守地回源(可接受)

- [ ] **Step 1: 写失败测试**

`backend/tests/data/test_cache.py`:

```python
import datetime as dt

from app.data.base import PriceProvider
from app.data.cache import CachedPriceProvider
from tests.helpers import make_bars


class CountingProvider(PriceProvider):
    def __init__(self, bars):
        self.bars = bars
        self.calls = 0

    def get_daily_bars(self, symbol, start, end):
        self.calls += 1
        mask = (self.bars.index.date >= start) & (self.bars.index.date <= end)
        return self.bars.loc[mask]


def test_second_identical_call_hits_cache(tmp_path):
    inner = CountingProvider(make_bars(start="2024-01-01", days=10))  # 至 2024-01-12(周五)
    p = CachedPriceProvider(inner, tmp_path)
    start, end = dt.date(2024, 1, 1), dt.date(2024, 1, 12)
    first = p.get_daily_bars("AAA", start, end)
    second = p.get_daily_bars("AAA", start, end)
    assert inner.calls == 1
    assert first.equals(second)
    assert len(first) == 10


def test_uncovered_range_refetches_and_merges(tmp_path):
    inner = CountingProvider(make_bars(start="2024-01-01", days=20))  # 至 2024-01-26
    p = CachedPriceProvider(inner, tmp_path)
    p.get_daily_bars("AAA", dt.date(2024, 1, 1), dt.date(2024, 1, 12))
    assert inner.calls == 1
    out = p.get_daily_bars("AAA", dt.date(2024, 1, 1), dt.date(2024, 1, 26))
    assert inner.calls == 2
    assert len(out) == 20
    # 合并后再次请求子区间应命中缓存
    p.get_daily_bars("AAA", dt.date(2024, 1, 8), dt.date(2024, 1, 19))
    assert inner.calls == 2


def test_subrange_served_from_cache(tmp_path):
    inner = CountingProvider(make_bars(start="2024-01-01", days=10))
    p = CachedPriceProvider(inner, tmp_path)
    p.get_daily_bars("AAA", dt.date(2024, 1, 1), dt.date(2024, 1, 12))
    sub = p.get_daily_bars("AAA", dt.date(2024, 1, 3), dt.date(2024, 1, 10))
    assert inner.calls == 1
    assert sub.index.min().date() >= dt.date(2024, 1, 3)
    assert sub.index.max().date() <= dt.date(2024, 1, 10)
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/data/test_cache.py -v`
Expected: FAIL(module not found)

- [ ] **Step 3: 实现**

`backend/app/data/cache.py`:

```python
import datetime as dt
from pathlib import Path

import pandas as pd

from app.data.base import PriceProvider


class CachedPriceProvider(PriceProvider):
    """parquet 本地缓存;命中范围直接切片,否则回源并合并写回。"""

    def __init__(self, inner: PriceProvider, cache_dir: Path):
        self._inner = inner
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def get_daily_bars(self, symbol: str, start: dt.date, end: dt.date):
        cached = self._load(symbol)
        if cached is not None and self._covers(cached, start, end):
            return self._slice(cached, start, end)
        fetched = self._inner.get_daily_bars(symbol, start, end)
        merged = self._merge(cached, fetched)
        if not merged.empty:
            merged.to_parquet(self._path(symbol))
        return self._slice(merged, start, end)

    def _path(self, symbol: str) -> Path:
        return self._dir / f"{symbol.upper()}.parquet"

    def _load(self, symbol: str):
        path = self._path(symbol)
        if not path.exists():
            return None
        return pd.read_parquet(path)

    @staticmethod
    def _covers(df: pd.DataFrame, start: dt.date, end: dt.date) -> bool:
        if df.empty:
            return False
        return df.index.min().date() <= start and df.index.max().date() >= end

    @staticmethod
    def _merge(cached, fetched) -> pd.DataFrame:
        if cached is None or cached.empty:
            return fetched
        merged = pd.concat([cached, fetched])
        merged = merged[~merged.index.duplicated(keep="last")]
        return merged.sort_index()

    @staticmethod
    def _slice(df: pd.DataFrame, start: dt.date, end: dt.date) -> pd.DataFrame:
        if df.empty:
            return df
        mask = (df.index.date >= start) & (df.index.date <= end)
        return df.loc[mask]
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/data/test_cache.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
cd /data1/common/haibotong/stock-agent
git add backend/app/data/cache.py backend/tests/data/test_cache.py
git commit -m "feat: parquet-backed price cache (M1 task 5)"
```

---

### Task 6: 筛选器框架(Rule / Screener)

**Files:**
- Create: `backend/app/screener/base.py`
- Test: `backend/tests/screener/test_screener_base.py`

**Interfaces:**
- Produces: `clamp01(x: float) -> float`;`RuleResult(score: float, detail: str)`(frozen dataclass);抽象类 `Rule`(类属性 `name: str`,方法 `evaluate(bars: pd.DataFrame) -> RuleResult`);`SymbolScore(symbol: str, total: float, parts: dict)`;`Screener(weighted_rules: list[tuple[Rule, float]])`,方法 `score_symbol(symbol, bars) -> SymbolScore`、`rank(bars_by_symbol: dict, top_n: int) -> list[SymbolScore]`
- 行为约定:规则抛异常按 0 分记不中断;score 裁剪到 [0,1];total 为权重归一化加权和

- [ ] **Step 1: 写失败测试**

`backend/tests/screener/test_screener_base.py`:

```python
import pytest

from app.screener.base import Rule, RuleResult, Screener, SymbolScore, clamp01
from tests.helpers import make_bars


class FixedRule(Rule):
    def __init__(self, name, score):
        self.name = name
        self._score = score

    def evaluate(self, bars):
        return RuleResult(self._score, f"fixed {self._score}")


class BoomRule(Rule):
    name = "boom"

    def evaluate(self, bars):
        raise ValueError("boom")


def test_clamp01():
    assert clamp01(-1) == 0.0
    assert clamp01(0.5) == 0.5
    assert clamp01(2.0) == 1.0


def test_weighted_total():
    s = Screener([(FixedRule("a", 1.0), 3.0), (FixedRule("b", 0.0), 1.0)])
    out = s.score_symbol("X", make_bars())
    assert out.total == pytest.approx(0.75)
    assert out.parts["a"].score == 1.0


def test_rule_exception_scores_zero():
    s = Screener([(BoomRule(), 1.0), (FixedRule("a", 1.0), 1.0)])
    out = s.score_symbol("X", make_bars())
    assert out.parts["boom"].score == 0.0
    assert "boom" in out.parts["boom"].detail
    assert out.total == pytest.approx(0.5)


def test_score_clamped():
    s = Screener([(FixedRule("hot", 5.0), 1.0)])
    assert s.score_symbol("X", make_bars()).total == 1.0


def test_rank_sorts_and_truncates():
    class Half(Rule):
        name = "a"

        def evaluate(self, bars):
            return RuleResult(0.5 if len(bars) < 5 else 1.0, "")

    s = Screener([(Half(), 1.0)])
    ranked = s.rank({"LOW": make_bars(days=3), "HIGH": make_bars(days=10)}, top_n=1)
    assert len(ranked) == 1
    assert ranked[0].symbol == "HIGH"


def test_empty_rules_rejected():
    with pytest.raises(ValueError):
        Screener([])
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/screener/test_screener_base.py -v`
Expected: FAIL(module not found)

- [ ] **Step 3: 实现**

`backend/app/screener/base.py`:

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd


def clamp01(x: float) -> float:
    return min(max(float(x), 0.0), 1.0)


@dataclass(frozen=True)
class RuleResult:
    score: float  # 0.0 - 1.0
    detail: str


class Rule(ABC):
    name: str = "rule"

    @abstractmethod
    def evaluate(self, bars: pd.DataFrame) -> RuleResult:
        """对单只标的的日线历史打分。数据不足时返回 score=0。"""


@dataclass(frozen=True)
class SymbolScore:
    symbol: str
    total: float  # 加权总分 0.0 - 1.0
    parts: dict  # rule name -> RuleResult


class Screener:
    """按 (Rule, weight) 组合打分并排序。规则抛异常按 0 分记,不中断整轮筛选。"""

    def __init__(self, weighted_rules: list):
        if not weighted_rules:
            raise ValueError("weighted_rules must not be empty")
        self._rules = weighted_rules
        self._weight_sum = sum(w for _, w in weighted_rules)

    def score_symbol(self, symbol: str, bars: pd.DataFrame) -> SymbolScore:
        parts = {}
        total = 0.0
        for rule, weight in self._rules:
            try:
                result = rule.evaluate(bars)
                result = RuleResult(clamp01(result.score), result.detail)
            except Exception as exc:
                result = RuleResult(0.0, f"error: {exc}")
            parts[rule.name] = result
            total += result.score * weight
        return SymbolScore(symbol, total / self._weight_sum, parts)

    def rank(self, bars_by_symbol: dict, top_n: int) -> list:
        scores = [self.score_symbol(sym, bars) for sym, bars in bars_by_symbol.items()]
        scores.sort(key=lambda s: s.total, reverse=True)
        return scores[:top_n]
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/screener/test_screener_base.py -v`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
cd /data1/common/haibotong/stock-agent
git add backend/app/screener/base.py backend/tests/screener/test_screener_base.py
git commit -m "feat: screener rule framework (M1 task 6)"
```

---

### Task 7: 趋势规则

**Files:**
- Create: `backend/app/screener/rules_trend.py`
- Test: `backend/tests/screener/test_rules_trend.py`

**Interfaces:**
- Consumes: `Rule`、`RuleResult`(Task 6),`sma`(Task 2)
- Produces: `TrendRule()`,`name = "trend"`;评分 = (收盘>SMA20) + (SMA20>SMA50) + (SMA50 高于 5 日前) 各 1/3;数据 < 60 根 → 0 分

- [ ] **Step 1: 写失败测试**

`backend/tests/screener/test_rules_trend.py`:

```python
import pytest

from app.screener.rules_trend import TrendRule
from tests.helpers import make_bars


def test_uptrend_scores_full():
    bars = make_bars(days=120, base=100.0, step=1.0)
    out = TrendRule().evaluate(bars)
    assert out.score == pytest.approx(1.0)


def test_downtrend_scores_zero():
    bars = make_bars(days=120, base=500.0, step=-1.0)
    out = TrendRule().evaluate(bars)
    assert out.score == pytest.approx(0.0)


def test_insufficient_data_scores_zero():
    out = TrendRule().evaluate(make_bars(days=30))
    assert out.score == 0.0
    assert "insufficient" in out.detail
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/screener/test_rules_trend.py -v`
Expected: FAIL(module not found)

- [ ] **Step 3: 实现**

`backend/app/screener/rules_trend.py`:

```python
import math

import pandas as pd

from app.screener.base import Rule, RuleResult
from app.screener.indicators import sma

MIN_BARS = 60


class TrendRule(Rule):
    """趋势:收盘>SMA20、SMA20>SMA50、SMA50 走高(对比 5 日前),各占 1/3。"""

    name = "trend"

    def evaluate(self, bars: pd.DataFrame) -> RuleResult:
        if len(bars) < MIN_BARS:
            return RuleResult(0.0, f"insufficient data ({len(bars)} bars)")
        close = bars["close"]
        s20 = sma(close, 20)
        s50 = sma(close, 50)
        values = [close.iloc[-1], s20.iloc[-1], s50.iloc[-1], s50.iloc[-6]]
        if any(math.isnan(v) for v in values):
            return RuleResult(0.0, "insufficient data (nan sma)")
        checks = {
            "close>sma20": values[0] > values[1],
            "sma20>sma50": values[1] > values[2],
            "sma50 rising": values[2] > values[3],
        }
        score = sum(checks.values()) / len(checks)
        detail = ", ".join(f"{k}={v}" for k, v in checks.items())
        return RuleResult(score, detail)
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/screener/test_rules_trend.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
cd /data1/common/haibotong/stock-agent
git add backend/app/screener/rules_trend.py backend/tests/screener/test_rules_trend.py
git commit -m "feat: trend screener rule (M1 task 7)"
```

---

### Task 8: 动量规则

**Files:**
- Create: `backend/app/screener/rules_momentum.py`
- Test: `backend/tests/screener/test_rules_momentum.py`

**Interfaces:**
- Consumes: `Rule`、`RuleResult`、`clamp01`(Task 6),`pct_return`、`rsi`(Task 2)
- Produces: `MomentumRule()`,`name = "momentum"`,score = 0.6×ret_score + 0.4×rsi_band_score;`rsi_band_score(value: float) -> float`(模块级函数,便于单测);数据 < 30 根 → 0 分

- [ ] **Step 1: 写失败测试**

`backend/tests/screener/test_rules_momentum.py`:

```python
import pytest

from app.screener.rules_momentum import MomentumRule, rsi_band_score
from tests.helpers import make_bars


def test_rsi_band_score_piecewise():
    assert rsi_band_score(25) == 0.0
    assert rsi_band_score(40) == pytest.approx(0.5)
    assert rsi_band_score(60) == 1.0
    assert rsi_band_score(75) == pytest.approx(0.5)
    assert rsi_band_score(90) == 0.0


def test_insufficient_data_scores_zero():
    out = MomentumRule().evaluate(make_bars(days=10))
    assert out.score == 0.0
    assert "insufficient" in out.detail


def test_uptrend_beats_downtrend():
    up = MomentumRule().evaluate(make_bars(days=60, base=100.0, step=1.0))
    # step=-5 保证 20 日收益 < -10%,ret_score 与 RSI 区间分都到 0
    down = MomentumRule().evaluate(make_bars(days=60, base=500.0, step=-5.0))
    assert up.score > down.score
    assert down.score == pytest.approx(0.0)
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/screener/test_rules_momentum.py -v`
Expected: FAIL(module not found)

- [ ] **Step 3: 实现**

`backend/app/screener/rules_momentum.py`:

```python
import math

import pandas as pd

from app.screener.base import Rule, RuleResult, clamp01
from app.screener.indicators import pct_return, rsi

MIN_BARS = 30


def rsi_band_score(value: float) -> float:
    """RSI 健康区间打分:<30→0,30-50 线性升,50-70→1,70-80 线性降,>80→0(过热)。"""
    if value < 30:
        return 0.0
    if value < 50:
        return (value - 30) / 20
    if value <= 70:
        return 1.0
    if value <= 80:
        return (80 - value) / 10
    return 0.0


class MomentumRule(Rule):
    """动量:0.6×20日收益(-10%~+20% 线性映射到 0~1) + 0.4×RSI 区间分。"""

    name = "momentum"

    def evaluate(self, bars: pd.DataFrame) -> RuleResult:
        if len(bars) < MIN_BARS:
            return RuleResult(0.0, f"insufficient data ({len(bars)} bars)")
        ret20 = pct_return(bars["close"], 20).iloc[-1]
        rsi14 = rsi(bars["close"], 14).iloc[-1]
        if math.isnan(ret20) or math.isnan(rsi14):
            return RuleResult(0.0, "nan inputs")
        ret_score = clamp01((ret20 + 0.10) / 0.30)
        score = 0.6 * ret_score + 0.4 * rsi_band_score(rsi14)
        return RuleResult(score, f"ret20={ret20:.2%}, rsi14={rsi14:.1f}")
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/screener/test_rules_momentum.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
cd /data1/common/haibotong/stock-agent
git add backend/app/screener/rules_momentum.py backend/tests/screener/test_rules_momentum.py
git commit -m "feat: momentum screener rule (M1 task 8)"
```

---

### Task 9: 量能规则

**Files:**
- Create: `backend/app/screener/rules_volume.py`
- Test: `backend/tests/screener/test_rules_volume.py`

**Interfaces:**
- Consumes: `Rule`、`RuleResult`、`clamp01`(Task 6)
- Produces: `VolumeRule()`,`name = "volume"`;ratio = 5日均量/60日均量,按 [0.5, 2.0]→[0,1] 线性映射;数据 < 60 根 → 0 分

- [ ] **Step 1: 写失败测试**

`backend/tests/screener/test_rules_volume.py`:

```python
import pytest

from app.screener.rules_volume import VolumeRule
from tests.helpers import make_bars


def test_constant_volume_scores_one_third():
    out = VolumeRule().evaluate(make_bars(days=80))
    assert out.score == pytest.approx((1.0 - 0.5) / 1.5, abs=1e-6)


def test_volume_surge_scores_full():
    bars = make_bars(days=80)
    bars.iloc[-5:, bars.columns.get_loc("volume")] = 10_000_000.0
    assert VolumeRule().evaluate(bars).score == 1.0


def test_insufficient_data_scores_zero():
    out = VolumeRule().evaluate(make_bars(days=30))
    assert out.score == 0.0
    assert "insufficient" in out.detail
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/screener/test_rules_volume.py -v`
Expected: FAIL(module not found)

- [ ] **Step 3: 实现**

`backend/app/screener/rules_volume.py`:

```python
import pandas as pd

from app.screener.base import Rule, RuleResult, clamp01

MIN_BARS = 60


class VolumeRule(Rule):
    """量能:近5日均量/近60日均量,0.5→0 分,2.0→1 分,线性映射。"""

    name = "volume"

    def evaluate(self, bars: pd.DataFrame) -> RuleResult:
        if len(bars) < MIN_BARS:
            return RuleResult(0.0, f"insufficient data ({len(bars)} bars)")
        v5 = float(bars["volume"].iloc[-5:].mean())
        v60 = float(bars["volume"].iloc[-60:].mean())
        if v60 <= 0:
            return RuleResult(0.0, "no volume")
        ratio = v5 / v60
        return RuleResult(clamp01((ratio - 0.5) / 1.5), f"vol5/vol60={ratio:.2f}")
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/screener/test_rules_volume.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
cd /data1/common/haibotong/stock-agent
git add backend/app/screener/rules_volume.py backend/tests/screener/test_rules_volume.py
git commit -m "feat: volume screener rule (M1 task 9)"
```

---

### Task 10: 股票池

**Files:**
- Create: `backend/app/screener/universe.py`
- Test: `backend/tests/screener/test_universe.py`

**Interfaces:**
- Produces: `DEFAULT_UNIVERSE: list[str]`(30 只美股大盘流动股);`load_universe(path=None) -> list[str]`——path 为 None 返回默认池;文件格式每行一个代码,空行与 `#` 注释忽略,统一大写;空文件抛 `ValueError`

- [ ] **Step 1: 写失败测试**

`backend/tests/screener/test_universe.py`:

```python
import pytest

from app.screener.universe import DEFAULT_UNIVERSE, load_universe


def test_default_universe():
    syms = load_universe(None)
    assert syms == DEFAULT_UNIVERSE
    assert len(syms) >= 20
    assert "AAPL" in syms
    assert syms is not DEFAULT_UNIVERSE  # 返回副本,防止调用方改坏默认池


def test_load_from_file(tmp_path):
    f = tmp_path / "u.txt"
    f.write_text("aapl\n# comment\n\nMSFT\n")
    assert load_universe(f) == ["AAPL", "MSFT"]


def test_empty_file_rejected(tmp_path):
    f = tmp_path / "u.txt"
    f.write_text("# only comments\n")
    with pytest.raises(ValueError):
        load_universe(f)
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/screener/test_universe.py -v`
Expected: FAIL(module not found)

- [ ] **Step 3: 实现**

`backend/app/screener/universe.py`:

```python
from pathlib import Path

DEFAULT_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "AMD", "NFLX",
    "CRM", "ORCL", "ADBE", "COST", "JPM", "V", "MA", "UNH", "LLY", "XOM",
    "WMT", "HD", "PG", "KO", "PEP", "BAC", "DIS", "CSCO", "INTC", "QCOM",
]


def load_universe(path=None) -> list:
    """从文件读股票池(每行一个代码,# 开头为注释);path 为 None 用默认池。"""
    if path is None:
        return list(DEFAULT_UNIVERSE)
    lines = Path(path).read_text().splitlines()
    symbols = [ln.strip().upper() for ln in lines]
    symbols = [s for s in symbols if s and not s.startswith("#")]
    if not symbols:
        raise ValueError(f"universe file {path} contains no symbols")
    return symbols
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/screener/test_universe.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
cd /data1/common/haibotong/stock-agent
git add backend/app/screener/universe.py backend/tests/screener/test_universe.py
git commit -m "feat: stock universe loader (M1 task 10)"
```

---

### Task 11: 模拟撮合 SimBroker

**Files:**
- Create: `backend/app/backtest/sim_broker.py`
- Create: `backend/tests/backtest/__init__.py`(空)
- Test: `backend/tests/backtest/test_sim_broker.py`

**Interfaces:**
- Produces: `Order(symbol: str, side: str, shares: int)`、`Fill(date: dt.date, symbol: str, side: str, shares: int, price: float)`(均 frozen dataclass);`SimBroker(cash: float, slippage_bps: float = 5.0)`,属性 `cash`、`positions`(副本 dict)、`fills`(累计列表),方法 `position(symbol) -> int`、`submit(order)`(side/shares 非法抛 ValueError)、`process_fills(date, open_prices: dict) -> list[Fill]`、`equity(close_prices: dict) -> float`(持仓缺价抛 KeyError)
- 撮合语义:T 日 submit 的订单在下一次 `process_fills`(T+1 开盘)成交;买入价 `open*(1+slip)`、卖出价 `open*(1-slip)`;买入按现金截断股数、卖出按持仓截断;无开盘价的订单丢弃;每次 process 后清空挂单

- [ ] **Step 1: 写失败测试**

`backend/tests/backtest/test_sim_broker.py`:

```python
import datetime as dt

import pytest

from app.backtest.sim_broker import Fill, Order, SimBroker

D = dt.date(2024, 1, 2)


def test_buy_fills_at_open_with_slippage():
    b = SimBroker(cash=10_000, slippage_bps=100)  # 1%
    b.submit(Order("AAPL", "buy", 10))
    fills = b.process_fills(D, {"AAPL": 100.0})
    assert fills == [Fill(D, "AAPL", "buy", 10, pytest.approx(101.0))]
    assert b.cash == pytest.approx(10_000 - 1010.0)
    assert b.position("AAPL") == 10


def test_buy_clamps_to_affordable_shares():
    b = SimBroker(cash=500, slippage_bps=100)
    b.submit(Order("AAPL", "buy", 10))
    fills = b.process_fills(D, {"AAPL": 100.0})
    assert fills[0].shares == 4  # int(500 // 101)
    assert b.cash == pytest.approx(500 - 4 * 101.0)


def test_sell_clamps_to_position():
    b = SimBroker(cash=10_000, slippage_bps=0)
    b.submit(Order("AAPL", "buy", 3))
    b.process_fills(D, {"AAPL": 100.0})
    b.submit(Order("AAPL", "sell", 5))
    fills = b.process_fills(dt.date(2024, 1, 3), {"AAPL": 110.0})
    assert fills[0].shares == 3
    assert b.position("AAPL") == 0
    assert b.cash == pytest.approx(10_000 - 300 + 330)


def test_sell_without_position_is_dropped():
    b = SimBroker(cash=1_000)
    b.submit(Order("AAPL", "sell", 5))
    assert b.process_fills(D, {"AAPL": 100.0}) == []


def test_missing_open_price_drops_order():
    b = SimBroker(cash=1_000)
    b.submit(Order("AAPL", "buy", 1))
    assert b.process_fills(D, {}) == []
    assert b.process_fills(dt.date(2024, 1, 3), {"AAPL": 100.0}) == []  # 挂单不跨日残留


def test_equity_and_missing_close_raises():
    b = SimBroker(cash=10_000, slippage_bps=0)
    b.submit(Order("AAPL", "buy", 10))
    b.process_fills(D, {"AAPL": 100.0})
    assert b.equity({"AAPL": 110.0}) == pytest.approx(9_000 + 1_100)
    with pytest.raises(KeyError):
        b.equity({})


def test_submit_validation():
    b = SimBroker(cash=1_000)
    with pytest.raises(ValueError):
        b.submit(Order("AAPL", "hold", 1))
    with pytest.raises(ValueError):
        b.submit(Order("AAPL", "buy", 0))
    with pytest.raises(ValueError):
        SimBroker(cash=0)
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/backtest/test_sim_broker.py -v`
Expected: FAIL(module not found)

- [ ] **Step 3: 实现**

`backend/app/backtest/sim_broker.py`:

```python
import datetime as dt
from dataclasses import dataclass


@dataclass(frozen=True)
class Order:
    symbol: str
    side: str  # "buy" | "sell"
    shares: int


@dataclass(frozen=True)
class Fill:
    date: dt.date
    symbol: str
    side: str
    shares: int
    price: float


class SimBroker:
    """模拟撮合:T 日提交的订单在下一次 process_fills(T+1 开盘)成交。"""

    def __init__(self, cash: float, slippage_bps: float = 5.0):
        if cash <= 0:
            raise ValueError("cash must be positive")
        self._cash = cash
        self._slip = slippage_bps / 10_000
        self._positions: dict = {}
        self._pending: list = []
        self.fills: list = []

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def positions(self) -> dict:
        return dict(self._positions)

    def position(self, symbol: str) -> int:
        return self._positions.get(symbol, 0)

    def submit(self, order: Order) -> None:
        if order.side not in ("buy", "sell"):
            raise ValueError(f"invalid side: {order.side}")
        if order.shares <= 0:
            raise ValueError("shares must be positive")
        self._pending.append(order)

    def process_fills(self, date: dt.date, open_prices: dict) -> list:
        """用当日开盘价撮合所有挂单;无开盘价(停牌等)的订单丢弃。"""
        todays: list = []
        for order in self._pending:
            price = open_prices.get(order.symbol)
            if price is None:
                continue
            fill = self._execute(order, date, float(price))
            if fill is not None:
                todays.append(fill)
        self._pending = []
        self.fills.extend(todays)
        return todays

    def _execute(self, order: Order, date: dt.date, open_price: float):
        if order.side == "buy":
            price = open_price * (1 + self._slip)
            shares = min(order.shares, int(self._cash // price))
            if shares <= 0:
                return None
            self._cash -= shares * price
            self._positions[order.symbol] = self.position(order.symbol) + shares
        else:
            price = open_price * (1 - self._slip)
            shares = min(order.shares, self.position(order.symbol))
            if shares <= 0:
                return None
            self._cash += shares * price
            remaining = self.position(order.symbol) - shares
            if remaining:
                self._positions[order.symbol] = remaining
            else:
                self._positions.pop(order.symbol, None)
        return Fill(date, order.symbol, order.side, shares, price)

    def equity(self, close_prices: dict) -> float:
        value = self._cash
        for sym, shares in self._positions.items():
            if sym not in close_prices:
                raise KeyError(f"missing close price for held symbol {sym}")
            value += shares * close_prices[sym]
        return value
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/backtest/test_sim_broker.py -v`
Expected: 7 passed

- [ ] **Step 5: 提交**

```bash
cd /data1/common/haibotong/stock-agent
git add backend/app/backtest/sim_broker.py backend/tests/backtest/test_sim_broker.py
git commit -m "feat: simulated broker with next-open fills (M1 task 11)"
```

---

### Task 12: 回测指标

**Files:**
- Create: `backend/app/backtest/metrics.py`
- Test: `backend/tests/backtest/test_metrics.py`

**Interfaces:**
- Consumes: `Fill`(Task 11,仅用其 `.side/.symbol/.shares/.price` 字段)
- Produces: `total_return(equity: pd.Series) -> float`;`max_drawdown(equity) -> float`(负数);`sharpe(equity) -> float`(日收益年化、零波动或样本<2 返回 0);`round_trips(fills) -> list[float]`(FIFO 配对,每笔卖出一个已实现盈亏);`win_rate(fills) -> float`

- [ ] **Step 1: 写失败测试**

`backend/tests/backtest/test_metrics.py`:

```python
import datetime as dt

import pandas as pd
import pytest

from app.backtest.metrics import max_drawdown, round_trips, sharpe, total_return, win_rate
from app.backtest.sim_broker import Fill

D = dt.date(2024, 1, 2)


def _fill(side, shares, price, symbol="AAA"):
    return Fill(D, symbol, side, shares, price)


def test_total_return():
    assert total_return(pd.Series([100.0, 110.0, 121.0])) == pytest.approx(0.21)


def test_max_drawdown():
    eq = pd.Series([100.0, 120.0, 90.0, 130.0])
    assert max_drawdown(eq) == pytest.approx(-0.25)


def test_sharpe_zero_for_flat_curve():
    assert sharpe(pd.Series([100.0] * 10)) == 0.0
    assert sharpe(pd.Series([100.0])) == 0.0


def test_sharpe_positive_for_rising_curve():
    eq = pd.Series([100.0 * (1.01 ** i) + (i % 2) for i in range(50)])
    assert sharpe(eq) > 0


def test_round_trips_fifo_partial():
    fills = [
        _fill("buy", 10, 100.0),
        _fill("buy", 10, 110.0),
        _fill("sell", 15, 120.0),
    ]
    assert round_trips(fills) == [pytest.approx(10 * 20.0 + 5 * 10.0)]


def test_win_rate():
    fills = [
        _fill("buy", 10, 100.0),
        _fill("sell", 10, 110.0),  # win
        _fill("buy", 10, 100.0, symbol="BBB"),
        _fill("sell", 10, 90.0, symbol="BBB"),  # loss
    ]
    assert win_rate(fills) == pytest.approx(0.5)
    assert win_rate([]) == 0.0
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/backtest/test_metrics.py -v`
Expected: FAIL(module not found)

- [ ] **Step 3: 实现**

`backend/app/backtest/metrics.py`:

```python
import math
from collections import defaultdict, deque

import pandas as pd

TRADING_DAYS_PER_YEAR = 252


def total_return(equity: pd.Series) -> float:
    return float(equity.iloc[-1] / equity.iloc[0] - 1)


def max_drawdown(equity: pd.Series) -> float:
    """最大回撤,负数(如 -0.25 表示回撤 25%)。"""
    return float((equity / equity.cummax() - 1).min())


def sharpe(equity: pd.Series) -> float:
    """日收益年化 Sharpe(无风险利率按 0)。零波动或样本不足返回 0。"""
    r = equity.pct_change().dropna()
    if len(r) < 2:
        return 0.0
    sd = r.std()
    if sd == 0 or math.isnan(sd):
        return 0.0
    return float(r.mean() / sd * math.sqrt(TRADING_DAYS_PER_YEAR))


def round_trips(fills) -> list:
    """FIFO 配对买卖,返回每笔卖出的已实现盈亏。"""
    lots = defaultdict(deque)  # symbol -> deque of [shares, buy_price]
    pnls = []
    for f in fills:
        if f.side == "buy":
            lots[f.symbol].append([f.shares, f.price])
            continue
        remaining = f.shares
        pnl = 0.0
        queue = lots[f.symbol]
        while remaining > 0 and queue:
            lot = queue[0]
            take = min(lot[0], remaining)
            pnl += take * (f.price - lot[1])
            lot[0] -= take
            remaining -= take
            if lot[0] == 0:
                queue.popleft()
        pnls.append(pnl)
    return pnls


def win_rate(fills) -> float:
    pnls = round_trips(fills)
    if not pnls:
        return 0.0
    return sum(1 for p in pnls if p > 0) / len(pnls)
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/backtest/test_metrics.py -v`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
cd /data1/common/haibotong/stock-agent
git add backend/app/backtest/metrics.py backend/tests/backtest/test_metrics.py
git commit -m "feat: backtest performance metrics (M1 task 12)"
```

---

### Task 13: 回测引擎

**Files:**
- Create: `backend/app/backtest/engine.py`
- Test: `backend/tests/backtest/test_engine.py`

**Interfaces:**
- Consumes: `ReplayPriceProvider`(Task 3)、`Order/SimBroker`(Task 11)、metrics 函数(Task 12)、`SymbolScore`(Task 6)
- Produces: `BacktestConfig(start: dt.date, end: dt.date, initial_cash=100_000.0, max_positions=5, min_score=0.5, lookback_days=250, slippage_bps=5.0)`;`BacktestResult(equity_curve: pd.Series, fills: list, metrics: dict)`;`BacktestEngine(bars_by_symbol: dict, screener, config)`,`run() -> BacktestResult`。engine 对 screener 只依赖鸭子类型方法 `rank(bars_by_symbol, top_n)`
- 循环语义:对每个交易日 T——先用 T 日开盘价撮合昨日挂单;再把 ReplayProvider 拨到 T,用 ≤T 的数据 rank;持仓不在目标列表 → 全数卖出挂单;目标未持有 → 按 `equity/max_positions` 预算、T 日收盘价估算股数买入挂单;记录 T 日收盘净值。metrics 键:`total_return/max_drawdown/sharpe/win_rate/num_fills`;区间内无交易日抛 `ValueError`

- [ ] **Step 1: 写失败测试**

`backend/tests/backtest/test_engine.py`:

```python
import datetime as dt

import pytest

from app.backtest.engine import BacktestConfig, BacktestEngine
from app.screener.base import SymbolScore
from tests.helpers import make_bars


class ScriptedScreener:
    """第 i 次 rank 返回脚本第 i 项选股(score=1.0);脚本耗尽沿用最后一项。"""

    def __init__(self, picks):
        self.picks = picks
        self.calls = 0

    def rank(self, bars_by_symbol, top_n):
        pick = self.picks[min(self.calls, len(self.picks) - 1)]
        self.calls += 1
        if pick is None or pick not in bars_by_symbol:
            return []
        return [SymbolScore(pick, 1.0, {})]


def _bars():
    return {
        "AAA": make_bars(start="2024-01-01", days=10, base=100.0),
        "BBB": make_bars(start="2024-01-01", days=10, base=50.0),
    }


def _cfg(**kw):
    defaults = dict(
        start=dt.date(2024, 1, 1),
        end=dt.date(2024, 1, 12),
        initial_cash=10_000.0,
        max_positions=1,
        min_score=0.5,
        lookback_days=30,
        slippage_bps=0.0,
    )
    defaults.update(kw)
    return BacktestConfig(**defaults)


def test_buys_pick_next_open():
    result = BacktestEngine(_bars(), ScriptedScreener(["AAA"]), _cfg()).run()
    assert result.fills
    f = result.fills[0]
    assert (f.symbol, f.side) == ("AAA", "buy")
    assert f.date == dt.date(2024, 1, 2)  # T 日决策,T+1 开盘成交
    # 决策日收盘 100 → 目标 100 股;次日开盘 100.5,现金只够 99 股
    assert f.shares == 99
    assert f.price == pytest.approx(100.5)


def test_equity_curve_tracks_position():
    result = BacktestEngine(_bars(), ScriptedScreener(["AAA"]), _cfg()).run()
    eq = result.equity_curve
    assert len(eq) == 10
    assert eq.iloc[0] == pytest.approx(10_000.0)  # 首日只挂单未成交
    # 期末:现金 10000-99*100.5=49.5,持仓 99 股 × 期末收盘 109
    assert eq.iloc[-1] == pytest.approx(49.5 + 99 * 109.0)
    for key in ("total_return", "max_drawdown", "sharpe", "win_rate", "num_fills"):
        assert key in result.metrics


def test_sells_when_dropped_from_targets():
    picks = ["AAA", "AAA", "BBB"]
    result = BacktestEngine(_bars(), ScriptedScreener(picks), _cfg()).run()
    sides = [(f.symbol, f.side) for f in result.fills]
    assert ("AAA", "buy") in sides
    assert ("AAA", "sell") in sides
    assert ("BBB", "buy") in sides


def test_no_trades_when_below_min_score():
    result = BacktestEngine(_bars(), ScriptedScreener([None]), _cfg()).run()
    assert result.fills == []
    assert result.equity_curve.iloc[-1] == pytest.approx(10_000.0)


def test_empty_range_raises():
    with pytest.raises(ValueError):
        BacktestEngine(_bars(), ScriptedScreener(["AAA"]),
                       _cfg(start=dt.date(2030, 1, 1), end=dt.date(2030, 1, 5))).run()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/backtest/test_engine.py -v`
Expected: FAIL(module not found)

- [ ] **Step 3: 实现**

`backend/app/backtest/engine.py`:

```python
import datetime as dt
from dataclasses import dataclass

import pandas as pd

from app.backtest.metrics import max_drawdown, sharpe, total_return, win_rate
from app.backtest.sim_broker import Order, SimBroker
from app.data.replay import ReplayPriceProvider


@dataclass(frozen=True)
class BacktestConfig:
    start: dt.date
    end: dt.date
    initial_cash: float = 100_000.0
    max_positions: int = 5
    min_score: float = 0.5
    lookback_days: int = 250
    slippage_bps: float = 5.0


@dataclass(frozen=True)
class BacktestResult:
    equity_curve: pd.Series
    fills: list
    metrics: dict


class BacktestEngine:
    """日线事件循环:T 日收盘后用 ≤T 数据决策,订单 T+1 开盘成交。"""

    def __init__(self, bars_by_symbol: dict, screener, config: BacktestConfig):
        self._bars = bars_by_symbol
        self._screener = screener  # 只依赖 .rank(bars_by_symbol, top_n)
        self._cfg = config

    def run(self) -> BacktestResult:
        cfg = self._cfg
        calendar = self._calendar()
        if not calendar:
            raise ValueError("no trading days in backtest range")
        provider = ReplayPriceProvider(self._bars)
        broker = SimBroker(cash=cfg.initial_cash, slippage_bps=cfg.slippage_bps)
        last_close: dict = {}
        equity_points: dict = {}

        for ts in calendar:
            today = ts.date()
            broker.process_fills(today, self._prices_at(ts, "open"))
            last_close.update(self._prices_at(ts, "close"))

            provider.set_as_of(today)
            start = today - dt.timedelta(days=cfg.lookback_days)
            history = {sym: provider.get_daily_bars(sym, start, today) for sym in self._bars}
            scores = self._screener.rank(history, top_n=cfg.max_positions)
            targets = [s.symbol for s in scores if s.total >= cfg.min_score]

            for sym in list(broker.positions):
                if sym not in targets:
                    broker.submit(Order(sym, "sell", broker.position(sym)))

            budget = broker.equity(last_close) / cfg.max_positions
            for sym in targets:
                if broker.position(sym) == 0 and sym in last_close:
                    shares = int(budget // last_close[sym])
                    if shares > 0:
                        broker.submit(Order(sym, "buy", shares))

            equity_points[ts] = broker.equity(last_close)

        equity = pd.Series(equity_points).sort_index()
        return BacktestResult(
            equity_curve=equity,
            fills=list(broker.fills),
            metrics={
                "total_return": total_return(equity),
                "max_drawdown": max_drawdown(equity),
                "sharpe": sharpe(equity),
                "win_rate": win_rate(broker.fills),
                "num_fills": float(len(broker.fills)),
            },
        )

    def _calendar(self) -> list:
        dates = set()
        for df in self._bars.values():
            for ts in df.index:
                if self._cfg.start <= ts.date() <= self._cfg.end:
                    dates.add(ts)
        return sorted(dates)

    def _prices_at(self, ts, column: str) -> dict:
        out = {}
        for sym, df in self._bars.items():
            if ts in df.index:
                out[sym] = float(df.at[ts, column])
        return out
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/backtest/test_engine.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
cd /data1/common/haibotong/stock-agent
git add backend/app/backtest/engine.py backend/tests/backtest/test_engine.py
git commit -m "feat: daily-bar backtest engine (M1 task 13)"
```

---

### Task 14: 分析服务 + Markdown 报告

**Files:**
- Create: `backend/app/services/analysis_service.py`
- Create: `backend/app/report/markdown.py`
- Create: `backend/tests/services/__init__.py`、`backend/tests/report/__init__.py`(空)
- Test: `backend/tests/services/test_analysis_service.py`
- Test: `backend/tests/report/test_markdown.py`

**Interfaces:**
- Consumes: `Screener` + 三个规则(Task 6-9)、`PriceProvider`(Task 3)、`BacktestResult/BacktestConfig`(Task 13)
- Produces: `default_screener() -> Screener`(趋势 0.4 / 动量 0.4 / 量能 0.2);`run_screen(provider, symbols, top_n, lookback_days, as_of) -> list[SymbolScore]`;`render_screen_report(scores, as_of) -> str`;`render_backtest_report(result, config) -> str`

- [ ] **Step 1: 写失败测试**

`backend/tests/services/test_analysis_service.py`:

```python
import datetime as dt

from app.data.base import PriceProvider
from app.screener.base import Screener
from app.services.analysis_service import default_screener, run_screen
from tests.helpers import make_bars


class FakeProvider(PriceProvider):
    def __init__(self):
        self.requests = []

    def get_daily_bars(self, symbol, start, end):
        self.requests.append((symbol, start, end))
        base = 100.0 if symbol == "GOOD" else 500.0
        step = 1.0 if symbol == "GOOD" else -1.0
        return make_bars(start="2024-01-01", days=120, base=base, step=step)


def test_default_screener_composition():
    s = default_screener()
    assert isinstance(s, Screener)


def test_run_screen_ranks_uptrend_first():
    provider = FakeProvider()
    as_of = dt.date(2024, 6, 28)
    scores = run_screen(provider, ["BAD", "GOOD"], top_n=2, lookback_days=400, as_of=as_of)
    assert [s.symbol for s in scores] == ["GOOD", "BAD"]
    assert scores[0].total > scores[1].total
    # 请求区间正确:start = as_of - lookback
    sym, start, end = provider.requests[0]
    assert end == as_of
    assert start == as_of - dt.timedelta(days=400)
```

`backend/tests/report/test_markdown.py`:

```python
import datetime as dt

import pandas as pd

from app.backtest.engine import BacktestConfig, BacktestResult
from app.report.markdown import render_backtest_report, render_screen_report
from app.screener.base import RuleResult, SymbolScore


def test_render_screen_report():
    scores = [
        SymbolScore("AAPL", 0.85, {"trend": RuleResult(1.0, "all up"), "volume": RuleResult(0.5, "ok")}),
        SymbolScore("MSFT", 0.60, {"trend": RuleResult(0.6, "mixed")}),
    ]
    text = render_screen_report(scores, dt.date(2026, 7, 17))
    assert "2026-07-17" in text
    assert "AAPL" in text and "MSFT" in text
    assert "0.850" in text
    assert "all up" in text


def test_render_backtest_report():
    result = BacktestResult(
        equity_curve=pd.Series([100.0, 110.0]),
        fills=[],
        metrics={"total_return": 0.10, "max_drawdown": -0.05, "sharpe": 1.5,
                 "win_rate": 0.6, "num_fills": 4.0},
    )
    config = BacktestConfig(start=dt.date(2024, 1, 1), end=dt.date(2024, 6, 30))
    text = render_backtest_report(result, config)
    assert "2024-01-01" in text and "2024-06-30" in text
    assert "10.00%" in text   # 总收益
    assert "-5.00%" in text   # 最大回撤
    assert "1.50" in text     # 夏普
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/services tests/report -v`
Expected: FAIL(module not found)

- [ ] **Step 3: 实现**

`backend/app/services/analysis_service.py`:

```python
import datetime as dt

from app.data.base import PriceProvider
from app.screener.base import Screener
from app.screener.rules_momentum import MomentumRule
from app.screener.rules_trend import TrendRule
from app.screener.rules_volume import VolumeRule


def default_screener() -> Screener:
    """默认权重:趋势 0.4 / 动量 0.4 / 量能 0.2。"""
    return Screener([(TrendRule(), 0.4), (MomentumRule(), 0.4), (VolumeRule(), 0.2)])


def run_screen(provider: PriceProvider, symbols, top_n: int, lookback_days: int, as_of: dt.date):
    screener = default_screener()
    start = as_of - dt.timedelta(days=lookback_days)
    bars = {sym: provider.get_daily_bars(sym, start, as_of) for sym in symbols}
    return screener.rank(bars, top_n)
```

`backend/app/report/markdown.py`:

```python
import datetime as dt


def render_screen_report(scores, as_of: dt.date) -> str:
    lines = [f"# 每日筛选报告 {as_of.isoformat()}", ""]
    lines += ["| 排名 | 代码 | 总分 | 明细 |", "|---|---|---|---|"]
    for i, s in enumerate(scores, 1):
        parts = "; ".join(f"{name}={r.score:.2f}" for name, r in s.parts.items())
        lines.append(f"| {i} | {s.symbol} | {s.total:.3f} | {parts} |")
    lines.append("")
    for s in scores:
        lines.append(f"## {s.symbol}")
        for name, r in s.parts.items():
            lines.append(f"- **{name}** ({r.score:.2f}): {r.detail}")
        lines.append("")
    return "\n".join(lines)


def render_backtest_report(result, config) -> str:
    m = result.metrics
    return "\n".join(
        [
            f"# 回测报告 {config.start.isoformat()} ~ {config.end.isoformat()}",
            "",
            f"- 初始资金: {config.initial_cash:,.0f}",
            f"- 总收益: {m['total_return']:.2%}",
            f"- 最大回撤: {m['max_drawdown']:.2%}",
            f"- 夏普(年化): {m['sharpe']:.2f}",
            f"- 胜率: {m['win_rate']:.2%}",
            f"- 成交笔数: {int(m['num_fills'])}",
            "",
        ]
    )
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/services tests/report -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
cd /data1/common/haibotong/stock-agent
git add backend/app/services backend/app/report backend/tests/services backend/tests/report
git commit -m "feat: analysis service and markdown reports (M1 task 14)"
```

---

### Task 15: CLI + README + 联网冒烟

**Files:**
- Create: `backend/app/cli.py`
- Create: `backend/README.md`
- Test: `backend/tests/test_cli.py`
- Test: `backend/tests/test_network_smoke.py`

**Interfaces:**
- Consumes: Task 1-14 全部公开接口
- Produces: `python -m app.cli screen [--universe F] [--top N] [--reports-dir D]` 与 `python -m app.cli backtest --start YYYY-MM-DD --end YYYY-MM-DD [--cash X] [--max-positions N] [--universe F] [--reports-dir D]`;函数 `build_parser()`、`cmd_screen(args, provider=None) -> int`、`cmd_backtest(args, provider=None) -> int`、`main(argv=None) -> int`(provider 参数用于测试注入)

- [ ] **Step 1: 写失败测试**

`backend/tests/test_cli.py`:

```python
import datetime as dt

from app.cli import build_parser, cmd_backtest, cmd_screen
from app.data.base import PriceProvider
from tests.helpers import make_bars


class FakeProvider(PriceProvider):
    def get_daily_bars(self, symbol, start, end):
        base = 100.0 if symbol == "AAA" else 50.0
        bars = make_bars(start="2024-01-01", days=120, base=base)
        mask = (bars.index.date >= start) & (bars.index.date <= end)
        return bars.loc[mask]


def _universe_file(tmp_path):
    f = tmp_path / "u.txt"
    f.write_text("AAA\nBBB\n")
    return f


def test_screen_writes_report(tmp_path, capsys):
    args = build_parser().parse_args(
        ["screen", "--universe", str(_universe_file(tmp_path)),
         "--top", "2", "--reports-dir", str(tmp_path / "reports")]
    )
    assert cmd_screen(args, provider=FakeProvider()) == 0
    reports = list((tmp_path / "reports").glob("screen_*.md"))
    assert len(reports) == 1
    out = capsys.readouterr().out
    assert "AAA" in out


def test_backtest_writes_report_and_curve(tmp_path, capsys):
    args = build_parser().parse_args(
        ["backtest", "--start", "2024-04-01", "--end", "2024-05-31",
         "--universe", str(_universe_file(tmp_path)),
         "--reports-dir", str(tmp_path / "reports")]
    )
    assert cmd_backtest(args, provider=FakeProvider()) == 0
    md = list((tmp_path / "reports").glob("backtest_*.md"))
    csv = list((tmp_path / "reports").glob("backtest_*.csv"))
    assert len(md) == 1 and len(csv) == 1
    assert "回测报告" in capsys.readouterr().out


def test_parser_requires_command():
    import pytest

    with pytest.raises(SystemExit):
        build_parser().parse_args([])
```

`backend/tests/test_network_smoke.py`:

```python
import datetime as dt

import pytest

from app.data.prices_yfinance import YFinancePriceProvider


@pytest.mark.network
def test_yfinance_real_fetch():
    """联网冒烟:默认跳过,pytest -m network 手动运行。"""
    end = dt.date.today()
    start = end - dt.timedelta(days=30)
    df = YFinancePriceProvider().get_daily_bars("AAPL", start, end)
    assert not df.empty
    assert {"open", "close"}.issubset(df.columns)
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest tests/test_cli.py -v`
Expected: FAIL(module not found)

- [ ] **Step 3: 实现**

`backend/app/cli.py`:

```python
import argparse
import datetime as dt
import sys
from pathlib import Path

from app.backtest.engine import BacktestConfig, BacktestEngine
from app.config import get_settings
from app.data.cache import CachedPriceProvider
from app.data.prices_yfinance import YFinancePriceProvider
from app.report.markdown import render_backtest_report, render_screen_report
from app.screener.universe import load_universe
from app.services.analysis_service import default_screener, run_screen


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stock-agent", description="M1 量化底座 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    screen = sub.add_parser("screen", help="运行筛选器并输出报告")
    screen.add_argument("--universe", type=Path, default=None, help="股票池文件,缺省用内置池")
    screen.add_argument("--top", type=int, default=None, help="输出前 N 名")
    screen.add_argument("--reports-dir", type=Path, default=None)

    bt = sub.add_parser("backtest", help="quant-only 回测")
    bt.add_argument("--start", type=dt.date.fromisoformat, required=True)
    bt.add_argument("--end", type=dt.date.fromisoformat, required=True)
    bt.add_argument("--cash", type=float, default=100_000.0)
    bt.add_argument("--max-positions", type=int, default=5)
    bt.add_argument("--universe", type=Path, default=None)
    bt.add_argument("--reports-dir", type=Path, default=None)
    return parser


def _default_provider(settings):
    return CachedPriceProvider(YFinancePriceProvider(), settings.cache_dir)


def _write_report(reports_dir: Path, filename: str, text: str) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / filename
    path.write_text(text)
    return path


def cmd_screen(args, provider=None) -> int:
    settings = get_settings()
    provider = provider or _default_provider(settings)
    as_of = dt.date.today()
    symbols = load_universe(args.universe)
    top_n = args.top or settings.top_n
    scores = run_screen(provider, symbols, top_n, settings.lookback_days, as_of)
    text = render_screen_report(scores, as_of)
    path = _write_report(args.reports_dir or settings.reports_dir,
                         f"screen_{as_of.strftime('%Y%m%d')}.md", text)
    print(text)
    print(f"[report saved] {path}")
    return 0


def cmd_backtest(args, provider=None) -> int:
    settings = get_settings()
    provider = provider or _default_provider(settings)
    symbols = load_universe(args.universe)
    config = BacktestConfig(start=args.start, end=args.end,
                            initial_cash=args.cash, max_positions=args.max_positions)
    fetch_start = args.start - dt.timedelta(days=config.lookback_days)
    bars = {sym: provider.get_daily_bars(sym, fetch_start, args.end) for sym in symbols}
    bars = {sym: df for sym, df in bars.items() if not df.empty}
    result = BacktestEngine(bars, default_screener(), config).run()
    text = render_backtest_report(result, config)
    reports_dir = args.reports_dir or settings.reports_dir
    name = f"backtest_{args.start.isoformat()}_{args.end.isoformat()}"
    path = _write_report(reports_dir, f"{name}.md", text)
    result.equity_curve.to_csv(reports_dir / f"{name}.csv", header=["equity"])
    print(text)
    print(f"[report saved] {path}")
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "screen":
        return cmd_screen(args)
    return cmd_backtest(args)


if __name__ == "__main__":
    sys.exit(main())
```

`backend/README.md`:

```markdown
# stock-agent backend(M1 量化底座)

## 环境

    cd backend
    ~/.local/bin/uv venv --python 3.12 .venv
    ~/.local/bin/uv pip install --python .venv/bin/python -e ".[dev]"

## 用法

    # 每日筛选(默认内置 30 只大盘股池,报告写入 reports/)
    .venv/bin/python -m app.cli screen --top 10

    # quant-only 回测
    .venv/bin/python -m app.cli backtest --start 2024-01-01 --end 2025-01-01

## 测试

    .venv/bin/pytest            # 全部离线测试
    .venv/bin/pytest -m network # 联网冒烟(需外网)

配置用环境变量覆盖,前缀 STOCKAGENT_(如 STOCKAGENT_TOP_N=5、STOCKAGENT_CACHE_DIR=/data1/cache)。
```

- [ ] **Step 4: 运行确认全量通过**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest -v`
Expected: 全部通过(network 标记项显示 deselected)

- [ ] **Step 5: 真机冒烟(可选,需外网)**

Run: `cd /data1/common/haibotong/stock-agent/backend && .venv/bin/pytest -m network -v`
Expected: 1 passed(若无外网,记录跳过原因,不阻塞验收)

- [ ] **Step 6: 提交**

```bash
cd /data1/common/haibotong/stock-agent
git add backend/app/cli.py backend/README.md backend/tests/test_cli.py backend/tests/test_network_smoke.py
git commit -m "feat: CLI entrypoints and README (M1 task 15)"
```

---

## 验收标准(M1 完成定义)

1. `cd backend && .venv/bin/pytest` 全绿(离线)
2. `python -m app.cli screen` 在有外网的机器上能产出真实的每日筛选 Markdown 报告
3. `python -m app.cli backtest --start 2024-01-01 --end 2025-01-01` 能产出回测报告与净值 CSV
4. 所有 `app/` 下单文件 < 200 行
5. 无未来函数:回测中数据访问全部经 `ReplayPriceProvider`

## M2 预告(另出计划)

MCP server(FastMCP)+ trading skill + OpenClaw cron 接入 + 新闻/财报数据源(`news_finnhub.py`、`fundamentals_edgar.py`、`sanitize.py`)+ 建议模式日报推送。
