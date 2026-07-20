"""请求/响应 Pydantic 模型。全部 `extra="forbid"`——未知字段直接 422,不静默丢弃。

安全红线:这里没有 as_of / prices 字段——approve 端点不接受任何请求体,
服务端 as_of 与取价永远无法被客户端覆盖(见 routes_orders.py)。
"""
import datetime as dt

from pydantic import BaseModel, ConfigDict


class ModeUpdate(BaseModel):
    """full_auto 必须显式 confirm_full_auto=True 才能开启(安全红线,set_mode 内强制)。"""

    model_config = ConfigDict(extra="forbid")
    mode: str
    confirm_full_auto: bool = False


class RiskParamsUpdate(BaseModel):
    """风控参数部分更新;字段名与 settings_repo.RISK_PARAM_FIELDS 一一对应。"""

    model_config = ConfigDict(extra="forbid")
    single_position_cap_pct: float | None = None
    total_position_cap_pct: float | None = None
    max_new_positions_per_day: int | None = None
    daily_loss_halt_pct: float | None = None
    cooldown_days: int | None = None
    initial_cash: float | None = None


class RejectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = "rejected by user"


class BacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: dt.date
    end: dt.date
    cash: float = 100_000
    max_positions: int = 5
    universe: list[str] | None = None
