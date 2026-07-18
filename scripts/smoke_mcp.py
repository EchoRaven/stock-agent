"""MCP stdio 冒烟:起真实 `python -m app.mcp.server` 子进程,list tools + 提交一条建议模式决定。

network-free:只调 submit_decision(纯 DB 落库,不触发行情/新闻抓取);DB 指到临时目录。
不依赖 OpenClaw 本体;client 用 fastmcp 自带的(不新增依赖)。

运行方式(必须从 backend/ 目录,保证子进程 `-m app.mcp.server` 可解析):
    cd /data1/common/haibotong/stock-agent/backend && .venv/bin/python ../scripts/smoke_mcp.py
"""
import asyncio
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

EXPECTED_TOOLS = {"run_screener", "get_stock_briefing", "submit_decision", "run_backtest"}


def _payload() -> dict:
    return {
        "symbol": "AAPL",
        "as_of": dt.date.today().isoformat(),
        "action": "hold",
        "confidence": 0.5,
        "committee": {
            "technical": {"summary": "冒烟:横盘"},
            "fundamental": {"summary": "冒烟:无变化"},
            "sentiment": {"summary": "冒烟:中性"},
            "bear": {"summary": "冒烟:缺乏上行催化"},
        },
        "chair": {"verdict": "观望", "bear_rebuttal": "同意空头,维持观望"},
    }


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="smoke-mcp-"))
    env = dict(os.environ, STOCKAGENT_DB_PATH=str(tmp / "smoke.db"))
    transport = StdioTransport(command=sys.executable, args=["-m", "app.mcp.server"], env=env)
    async with Client(transport) as client:
        tools = {t.name for t in await client.list_tools()}
        missing = EXPECTED_TOOLS - tools
        assert not missing, f"missing tools: {missing}"
        result = await client.call_tool("submit_decision", {"payload": _payload()})
        text = result.content[0].text
        assert "recorded" in text, f"unexpected result: {text}"
    print(f"[smoke] tools OK: {sorted(tools)}")
    print("[smoke] submit_decision recorded (advisory), db:", tmp / "smoke.db")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
