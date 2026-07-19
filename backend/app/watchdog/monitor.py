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
    """任一 watched job 不健康且当前非 advisory → 自动降级 advisory + 记 alert。

    只 flush,不 commit——commit 由调用方负责(生产为 cli_trading.cmd_watchdog),
    与全系统"业务函数只 flush;service/CLI 提交"的约定一致。
    """
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
    return {"healthy": not unhealthy, "mode_before": mode_before,
            "mode_after": get_mode(session), "downgraded": downgraded,
            "reasons": [v.reason for v in verdicts]}
