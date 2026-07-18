# stock-agent backend(M1 量化底座)

## 环境

    cd backend
    ~/.local/bin/uv venv --python 3.12 .venv
    ~/.local/bin/uv pip install --python .venv/bin/python -e ".[dev]"

## 用法

    # 每日筛选(默认内置 30 只大盘股池,报告写入 reports/)
    .venv/bin/python -m app.cli screen --top 10

    # quant-only 回测
    .venv/bin/python -m app.cli backtest --start 2024-01-01 --end 2025-01-01

    # M2 盘后日报(汇总当日 signals + decisions,落库并写 reports/daily_YYYYMMDD.md)
    .venv/bin/python -m app.cli report

    # MCP server(stdio,给 OpenClaw 用;接入步骤见仓库根 openclaw/setup.md)
    .venv/bin/python -m app.mcp.server

## 测试

    .venv/bin/pytest            # 全部离线测试
    .venv/bin/pytest -m network # 联网冒烟(需外网)

配置用环境变量覆盖,前缀 STOCKAGENT_(如 STOCKAGENT_TOP_N=5、STOCKAGENT_CACHE_DIR=/data1/cache)。
