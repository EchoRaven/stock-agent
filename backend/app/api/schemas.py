"""请求/响应 Pydantic 模型。全部 `extra="forbid"`——未知字段直接 422,不静默丢弃。

安全红线:这里没有 as_of / prices 字段——approve 端点不接受任何请求体,
服务端 as_of 与取价永远无法被客户端覆盖(见 routes_orders.py)。
"""
import datetime as dt

from pydantic import BaseModel, ConfigDict, Field


class ModeUpdate(BaseModel):
    """full_auto 必须显式 confirm_full_auto=True 才能开启(安全红线,set_mode 内强制)。"""

    model_config = ConfigDict(extra="forbid")
    mode: str
    confirm_full_auto: bool = False


class RiskParamsUpdate(BaseModel):
    """风控参数部分更新;字段名与 settings_repo.RISK_PARAM_FIELDS 一一对应。

    范围校验对应 SettingsRow 里的实际语义(见 app/store/models.py):
    *_pct 字段是仓位占比/回撤占比的小数(0.20 = 20%),合法区间 [0, 1];
    计数/天数字段非负,给个宽松但不失意义的上限;initial_cash 必须为正。
    """

    model_config = ConfigDict(extra="forbid")
    single_position_cap_pct: float | None = Field(default=None, ge=0, le=1)
    total_position_cap_pct: float | None = Field(default=None, ge=0, le=1)
    max_new_positions_per_day: int | None = Field(default=None, ge=0, le=1000)
    daily_loss_halt_pct: float | None = Field(default=None, ge=0, le=1)
    cooldown_days: int | None = Field(default=None, ge=0, le=3650)
    initial_cash: float | None = Field(default=None, gt=0, le=1_000_000_000)


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
