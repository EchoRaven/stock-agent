"""GET/POST /api/settings —— 薄壳,业务全在 store/repos/settings_repo.py。

安全红线:响应字段白名单(mode + 风控参数),绝不把 SettingsRow 整体 dump——
即使将来给 SettingsRow 加了密钥字段,这里也不会泄露(密钥实际上活在
app.config.Settings,与 DB 的 SettingsRow 是两个不同对象,从不混在一起)。
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.api.schemas import ModeUpdate, RiskParamsUpdate
from app.api.security import require_token
from app.store.models import SettingsRow
from app.store.repos.settings_repo import (RISK_PARAM_FIELDS, get_app_settings, set_mode,
                                           update_risk_params)

router = APIRouter(tags=["settings"])


def _settings_to_dict(row: SettingsRow) -> dict:
    out = {"mode": row.mode}
    for field in RISK_PARAM_FIELDS:
        out[field] = getattr(row, field)
    return out


@router.get("/settings")
def get_settings_route(session: Session = Depends(get_session)) -> dict:
    return _settings_to_dict(get_app_settings(session))


@router.post("/settings/mode", dependencies=[Depends(require_token)])
def set_mode_route(body: ModeUpdate, session: Session = Depends(get_session)) -> dict:
    """full_auto 未显式 confirm_full_auto 时 set_mode 内部拒绝(ValueError→400);
    这里不做任何绕过或额外放行——红线判定完全交给 settings_repo。"""
    try:
        row = set_mode(session, body.mode, confirm_full_auto=body.confirm_full_auto)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return _settings_to_dict(row)


@router.post("/settings/risk", dependencies=[Depends(require_token)])
def set_risk_route(body: RiskParamsUpdate, session: Session = Depends(get_session)) -> dict:
    fields = body.model_dump(exclude_unset=True)
    row = update_risk_params(session, **fields)
    session.commit()
    return _settings_to_dict(row)
