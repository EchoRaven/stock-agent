import datetime as dt

from sqlalchemy import (Boolean, Date, DateTime, Float, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class SignalRow(Base):
    """每日筛选快照,一行一个 (as_of, symbol)。"""

    __tablename__ = "signals"
    __table_args__ = (UniqueConstraint("as_of", "symbol", name="uq_signals_asof_symbol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    as_of: Mapped[dt.date] = mapped_column(Date, index=True)
    symbol: Mapped[str] = mapped_column(String(16))
    rank: Mapped[int] = mapped_column(Integer)
    total: Mapped[float] = mapped_column(Float)
    parts_json: Mapped[str] = mapped_column(Text, default="{}")


class DecisionRow(Base):
    """委员会结构化决定。mode 为模式开关字段(M2 恒为 advisory,M3 起分流)。"""

    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    as_of: Mapped[dt.date] = mapped_column(Date, index=True)
    symbol: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(8))
    confidence: Mapped[float] = mapped_column(Float)
    mode: Mapped[str] = mapped_column(String(16), default="advisory")
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class ReportRow(Base):
    """日报(markdown 全文落库,同日同类覆盖)。"""

    __tablename__ = "reports"
    __table_args__ = (UniqueConstraint("report_date", "kind", name="uq_reports_date_kind"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_date: Mapped[dt.date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(16), default="daily")
    content_md: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class SettingsRow(Base):
    """运行设置单例行(id 恒为 1)。安全红线:mode 的唯一真相在此,风控参数在此。"""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # 恒为 1
    mode: Mapped[str] = mapped_column(String(16), default="advisory")
    execution_backend: Mapped[str] = mapped_column(String(16), default="paper")
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
