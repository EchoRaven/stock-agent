"""GET/POST /api/execution —— 执行后端(broker)切换。

安全红线:UI 只能在 settings_repo.EXECUTION_BACKENDS(paper/futu_paper)之间切换——
两个都是纸面/模拟盘。这里没有、也永远不会有能触达真实资金的字段或分支;
真实交易(REAL)只能靠 FutuBroker 内部 env-only 的 STOCKAGENT_FUTU_ALLOW_REAL +
STOCKAGENT_FUTU_UNLOCK_PWD 硬门控(见 app/execution/futu_broker.py),与这个开关
完全无关。set_execution_backend 对非法值抛 ValueError,这里转 400,不做任何
额外放行/兜底。
"""
import socket

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.api.schemas import ExecutionBackendUpdate
from app.api.security import require_token
from app.config import get_settings
from app.store.repos.settings_repo import (EXECUTION_BACKENDS, get_execution_backend,
                                           set_execution_backend)

router = APIRouter(tags=["execution"])

_OPEND_CONNECT_TIMEOUT = 1.0


def _opend_reachable(host: str, port: int) -> bool:
    """对 (host, port) 做一次原始 TCP 探测,不依赖 futu-api。任何失败(拒连/超时/
    DNS 错误/未装 futu 均无关)一律视为不可达——展示用途,绝不影响下单路径。"""
    try:
        with socket.create_connection((host, port), timeout=_OPEND_CONNECT_TIMEOUT):
            return True
    except Exception:
        return False


def _execution_state(session: Session) -> dict:
    settings = get_settings()
    return {
        "backend": get_execution_backend(session),
        "available_backends": list(EXECUTION_BACKENDS),
        "futu": {
            "host": settings.futu_host,
            "port": settings.futu_port,
            "trd_env": settings.futu_trd_env,
            "allow_real": settings.futu_allow_real,
            "opend_reachable": _opend_reachable(settings.futu_host, settings.futu_port),
        },
    }


@router.get("/execution")
def get_execution_route(session: Session = Depends(get_session)) -> dict:
    return _execution_state(session)


@router.post("/execution/backend", dependencies=[Depends(require_token)])
def set_execution_backend_route(body: ExecutionBackendUpdate,
                                session: Session = Depends(get_session)) -> dict:
    try:
        set_execution_backend(session, body.backend)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return _execution_state(session)
