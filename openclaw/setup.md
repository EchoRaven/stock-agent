# OpenClaw 接入步骤(M2)

前置:`backend/.venv` 已装好(见 backend/README.md),OpenClaw CLI 已全局安装。

## 1. 注册 MCP server

在 OpenClaw 的 MCP 配置(mcp servers 配置文件或 `openclaw mcp add`)加入:

```json
{
  "mcpServers": {
    "stock-backend": {
      "command": "/data1/common/haibotong/stock-agent/backend/.venv/bin/python",
      "args": ["-m", "app.mcp.server"],
      "cwd": "/data1/common/haibotong/stock-agent/backend",
      "env": {
        "STOCKAGENT_DB_PATH": "/data1/common/haibotong/stock-agent/backend/stockagent.db",
        "STOCKAGENT_FINNHUB_API_KEY": "<你的 finnhub key,可留空>",
        "STOCKAGENT_EDGAR_USER_AGENT": "stock-agent tonghaibo020@gmail.com"
      }
    }
  }
}
```

要点:
- `cwd` 必须是 backend/,否则 `-m app.mcp.server` 解析不到包。
- EDGAR 的 User-Agent 必须带联系方式(SEC 要求)。
- finnhub key 留空时新闻为空但流程不崩(后端已兜底)。

## 2. 安装 trading skill

把 `openclaw/skills/trading/` 拷贝(或软链)到 OpenClaw 的 skills 目录,
确认会话内可见名为 `trading` 的 skill。

## 3. 配置 cron(两条,详见 cron.md)

- 盘前分析:唤起 agent 执行 trading skill 全流程。
- 盘后日报:运行 `python -m app.cli report` 并把生成的 markdown 推送到用户渠道。

## 4. 冒烟验证(不依赖 OpenClaw)

```bash
cd /data1/common/haibotong/stock-agent/backend
.venv/bin/python ../scripts/smoke_mcp.py   # network-free:list tools + 提交一条建议模式决定
```
