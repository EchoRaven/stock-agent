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
