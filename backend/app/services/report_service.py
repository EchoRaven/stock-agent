import datetime as dt
from pathlib import Path

from sqlalchemy.orm import Session

from app.report.daily import render_daily_report
from app.store.repos.decision_repo import get_decisions
from app.store.repos.report_repo import save_report
from app.store.repos.signal_repo import get_signals


def build_daily_report(session: Session, report_date: dt.date) -> str:
    return render_daily_report(report_date,
                               get_signals(session, report_date),
                               get_decisions(session, report_date))


def generate_daily_report(session: Session, report_date: dt.date, reports_dir: Path) -> tuple:
    """生成当日日报:落库(同日覆盖)+ 写文件 daily_YYYYMMDD.md。返回 (markdown, 路径)。"""
    text = build_daily_report(session, report_date)
    save_report(session, report_date, text, kind="daily")
    session.commit()
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"daily_{report_date.strftime('%Y%m%d')}.md"
    path.write_text(text)
    return text, path
