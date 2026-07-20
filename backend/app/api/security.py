"""File-backed shared-secret token gate for state-changing REST routes.

安全背景:approve/reject 不带请求体,浏览器会把它们当 CORS "simple request"
处理——不触发预检,main.py 的 Origin 白名单永远不会被咨询。结果是用户访问的
任意网站都能悄悄 POST 到 http://127.0.0.1:<port>/api/orders/<id>/approve,
绕过 semi_auto 的人工确认闸门。

方案:标准的 localhost 控制面 token 模式(参考 Jupyter 的 token 机制)。跨站页面
既发不出自定义 header(会触发预检,被既有 Origin 白名单挡住),也读不到本地
token 文件——两条腿都断了,CSRF 通道闭合。所有状态变更(POST)路由都挂
`Depends(require_token)`;只读 GET 路由维持开放。
"""
import os
import secrets
from pathlib import Path

from fastapi import Header, HTTPException

from app.config import get_settings

_TOKEN_CACHE: dict[Path, str] = {}


def _token_path() -> Path:
    return get_settings().db_path.parent / ".api_token"


def _load_or_create_token(path: Path) -> str:
    """Read the token from disk, or generate+persist a new one. Stable per process
    (cached by resolved path) so repeated calls don't regenerate/re-read needlessly."""
    if path in _TOKEN_CACHE:
        return _TOKEN_CACHE[path]

    if path.exists():
        token = path.read_text().strip()
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(32)
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, token.encode("utf-8"))
        finally:
            os.close(fd)

    _TOKEN_CACHE[path] = token
    return token


def current_token() -> str:
    """当前进程使用的共享密钥,供未来前端/CLI 读取用。绝不打印/记日志。"""
    return _load_or_create_token(_token_path())


def require_token(
    x_stock_agent_token: str | None = Header(default=None, alias="X-Stock-Agent-Token"),
) -> None:
    """状态变更路由的门禁依赖。测试里可通过
    `app.dependency_overrides[require_token] = lambda: None` 整体绕过。"""
    expected = current_token()
    if x_stock_agent_token is None or not secrets.compare_digest(x_stock_agent_token, expected):
        raise HTTPException(status_code=403,
                            detail="missing or invalid X-Stock-Agent-Token")
