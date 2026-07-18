import datetime as dt

from sqlalchemy import Date, DateTime, Float, Integer, String, Text, UniqueConstraint
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
