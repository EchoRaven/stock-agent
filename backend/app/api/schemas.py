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


class ExecutionBackendUpdate(BaseModel):
    """安全红线:合法值只有 settings_repo.EXECUTION_BACKENDS(paper/futu_paper)——
    故意不在这里写 Literal 枚举(避免两处真相),校验全权委托 set_execution_backend,
    任何其它值(含 "real"/"futu_real")在那里被 ValueError 拒绝→routes_execution.py 转 400。
    """

    model_config = ConfigDict(extra="forbid")
    backend: str


class SignalsRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    universe: list[str] | None = None
    top_n: int | None = Field(default=None, ge=1, le=500)


class TradeCycleRequest(BaseModel):
    """POST /api/trade/cycle 的请求体。安全红线:max_eval 必须有界——该端点每评估
    一个标的就是一次行情+新闻抓取 + 一次(可能付费的)Gemini 调用,上限兼作
    费用/配额刹车,同 SentimentRequest 的 days/max_items。"""

    model_config = ConfigDict(extra="forbid")
    universe: list[str] | None = None
    max_eval: int | None = Field(default=None, ge=1, le=200)
    settle: bool = True


class SentimentRequest(BaseModel):
    """安全红线:days/max_items 必须有界——未加界的 days(如 999999999)会让
    dt.timedelta 溢出触发未处理 500,且该端点触发付费 Gemini/新闻调用,
    上限还兼作费用/配额刹车。"""

    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1)
    days: int = Field(default=7, ge=1, le=90)
    max_items: int = Field(default=10, ge=1, le=50)
