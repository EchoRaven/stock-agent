# stock-agent

一个**美股波段(swing)交易 agent**:量化筛选 + LLM 委员会定夺,带 Web UI、模拟盘、服务端风控闸门、以及一套 agent 记忆/知识系统。**默认只跑模拟盘,不碰真钱。**

> ⚠️ 仅供个人学习/研究。所有"策略实验"结论多为**诚实的负面结果**(见 `docs/`),不构成投资建议。接实盘是需你显式开启的独立步骤。

## 快速开始

```bash
./run.sh          # 启动后端(:8000,仅本机)+ 前端(:3000)
# 打开 http://localhost:3000
```

需要:`uv`(Python 3.12 环境)、Node.js。首次会自动 `npm install`。
LLM 功能需在 `backend/.env` 设 `STOCKAGENT_GEMINI_API_KEY`(git-ignored)。

## 是什么

- **决策核心 = 量化筛选 + LLM 委员会**:每天筛选器(趋势/动量/量能)选出候选,Gemini 四角色委员会(技术/基本面/情绪/空头 + 主席须回应空头)对候选定夺,产出结构化决策。
- **四种模式(DB 唯一真相,UI 可切)**:`advisory` 只记录 / `semi_auto` 人工在 UI 逐单批准 / `full_auto` 过闸门后自动在模拟盘成交 / 回测。
- **服务端风控闸门(唯一权威)**:LLM 只建议;每笔买卖必过 RiskGate(单票/总仓上限、日亏熔断、冷却期、新开仓上限、stale-quote fail-safe)。委员会说"全买",闸门照样按规则拦。
- **模拟盘**:自建 PaperBroker(下一开盘价成交,盘前回退最近收盘),DB 资金账本,资金只在现金↔持仓间流转——**系统内无任何转账/出金方法**(有守卫测试)。
- **Web UI**(Next.js):Dashboard / Signals / **个股详情(曲线·指标·新闻·基本面·AI 委员会分析)** / Orders(批准-拒绝) / Backtest / Execution(券商后端切换) / **Memory(知识库)** / **History(业绩追踪)** / Settings。

## agent 记忆 / 知识系统

- **知识库**:结构化条目(insight / factor / trade_review / market_note),播种了本项目 4 轮策略实验的真实结论;委员会决策时把相关知识 + 该票历史决策作**建议性上下文**读入(不碰闸门)。
- **交易复盘**:平仓自动算已实现盈亏,生成 post-mortem 写回记忆。
- **业绩追踪**:把复盘聚合成胜率/累计盈亏曲线——用来**随时间验证委员会到底行不行**。
- **自主因子挖掘(证据门槛)**:LLM 只从固定因子目录里选**结构化参数**(绝不执行 LLM 代码),每个提案自动两窗口回测,**只有稳健改善的才记为 validated,多数被证伪**——门槛阻止 agent 相信没验证的因子。

## 安全红线(勿削弱)

1. 风控闸门服务端确定性执行,LLM/agent 只能建议;`as_of`/价格一律服务端派生,payload 无通道。
2. 系统内无转账/出金方法(全树关键词守卫);MCP 仅只读/建议工具,无下单/批准工具。
3. mode 唯一真相在 DB;`full_auto` 需显式二次确认。
4. 日亏损熔断持久化 + stale-quote fail-safe。
5. 改状态的 HTTP 接口需 file-backed token(防 CSRF);后端仅绑 127.0.0.1;LLM 输出/外部新闻一律 clamp + 注入定界。
6. 接实盘(富途 OpenD 适配器)默认模拟盘,REAL 硬门(需 env `STOCKAGENT_FUTU_ALLOW_REAL` + 解锁密码);**agent 绝不自动动真钱**。见 `docs/futu_setup.md`。

## 结构

```
backend/   FastAPI + FastMCP + SQLAlchemy/SQLite;app/{screener,backtest,risk,execution,services,factors,llm,api,mcp,store}
frontend/  Next.js + TS + Tailwind + lightweight-charts;浏览器只同源调服务端代理(token 不进浏览器)
docs/      设计 spec + 各轮策略实验的诚实报告
```

测试:`cd backend && uv run pytest -q`(660+ 离线用例;联网用例用 `-m network`)。
