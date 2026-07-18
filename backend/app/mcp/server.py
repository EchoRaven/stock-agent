"""FastMCP server 装配:`python -m app.mcp.server` 以 stdio 启动。业务不在这里写。"""
from fastmcp import FastMCP

from app.mcp.tool_backtest import run_backtest
from app.mcp.tool_briefing import get_stock_briefing
from app.mcp.tool_decision import submit_decision
from app.mcp.tool_screener import run_screener


def build_server() -> FastMCP:
    mcp = FastMCP("stock-agent")
    for fn in (run_screener, get_stock_briefing, submit_decision, run_backtest):
        mcp.tool(fn)
    return mcp


def main() -> None:
    build_server().run()  # 默认 stdio transport


if __name__ == "__main__":
    main()
