from app.execution.order_manager import list_pending
from app.mcp import runtime


def get_pending_orders() -> dict:
    """只读:列出待人工确认的订单队列(agent 可汇报,不可批准)。

    安全红线:批准/拒绝只能由人在 CLI/Web UI 完成——系统不提供自动批准的 MCP 工具。
    """
    with runtime.open_session() as session:
        return {"pending": list_pending(session)}
