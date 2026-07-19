import asyncio

from fastmcp import FastMCP

from app.mcp.server import build_server


def test_server_registers_all_tools():
    server = build_server()
    assert isinstance(server, FastMCP)
    tools = asyncio.run(server.list_tools())
    assert {"run_screener", "get_stock_briefing", "submit_decision",
            "run_backtest", "get_pending_orders"} == {t.name for t in tools}


def test_no_approval_or_fund_egress_tools_exposed():
    # 红线:不存在自动批准工具(人审是半自动的意义);不存在资金转出工具
    tools = asyncio.run(build_server().list_tools())
    names = " ".join(t.name.lower() for t in tools)
    for forbidden in ("approve", "confirm", "cancel", "transfer", "withdraw", "deposit"):
        assert forbidden not in names
