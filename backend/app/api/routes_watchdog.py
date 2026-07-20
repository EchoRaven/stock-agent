"""POST /api/watchdog —— cron 心跳检查触发口(镜像 app/cli_trading.cmd_watchdog)。

token 门禁:check_and_enforce 在不健康时会自动把 mode 降级为 advisory 并落
alert——这是状态变更动作,不是只读轮询。
"""
import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.api.security import require_token
from app.watchdog.monitor import check_and_enforce

router = APIRouter(tags=["watchdog"])


@router.post("/watchdog", dependencies=[Depends(require_token)])
def run_watchdog_route(session: Session = Depends(get_session)) -> dict:
    result = check_and_enforce(session, dt.datetime.now(dt.UTC).replace(tzinfo=None))
    session.commit()
    return result
