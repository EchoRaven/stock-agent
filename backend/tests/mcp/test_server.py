import asyncio

from fastmcp import FastMCP

from app.mcp.server import build_server


def test_server_registers_all_tools():
    server = build_server()
    assert isinstance(server, FastMCP)
    tools = asyncio.run(server.list_tools())
    assert {"run_screener", "get_stock_briefing", "submit_decision",
            "run_backtest"} == {t.name for t in tools}
