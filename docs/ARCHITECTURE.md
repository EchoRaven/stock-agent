# 技术架构 / Architecture

> 本文描述 stock-agent 的**技术路线与系统架构**:各组件职责、数据如何流动、安全模型如何强制。
> 面向新加入的开发者与需要理解整体设计的人。配套文档:[ROADMAP.md](ROADMAP.md)(路线图)、[PROGRESS.md](PROGRESS.md)(进度/评测结论)。
>
> 最后更新:2026-08(commit 提交数 ~164,后端 785 离线测试)。

---

## 1. 一句话技术路线

**量化筛选(便宜、确定性)先把宽泛的股票池收窄成少量候选,再由 LLM 四角色委员会(贵、有判断力)对候选逐一定夺;委员会只出建议,真正决定成交与否的唯一权威是服务端确定性风控闸门;所有决策与成交都可被前瞻收益记分卡回测,以随时间验证"委员会到底行不行"。默认只跑模拟盘。**

这条路线的核心取舍:
- **两级漏斗**——不对全池调用 LLM(成本不可控),而是让确定性筛选器承担粗筛,LLM 只做细活。
- **建议与授权分离**——LLM 永远不可信,它的输出经严格 clamp;是否下单、下多少股,全部由服务端在 LLM 之外决定。
- **测量先于优化**——先把"决策好不好"量出来(记分卡 + 历史回放评测),再谈调优,避免拿单一行情过拟合。

---

## 2. 系统组成(自底向上)

```
┌─────────────────────────────────────────────────────────────────┐
│  前端 Next.js (:3000)  —— 浏览器只同源调 /api/backend 代理        │
│  11 页:Dashboard/Signals/Picks/Watchlist/Stock/Orders/          │
│         Backtest/Execution/Memory/History/Settings               │
└───────────────┬─────────────────────────────────────────────────┘
                │  同源代理转发(token 注入在服务端,不进浏览器)
┌───────────────▼─────────────────────────────────────────────────┐
│  后端 FastAPI (:8000, 仅绑 127.0.0.1) + FastMCP                   │
│  api/ 33 个端点(只读 GET 无门禁;改状态 POST/DELETE 需 token)    │
├──────────────────────────────────────────────────────────────────┤
│  services/  编排层(14 个):committee / decision / trade_cycle /  │
│             briefing / market_data / market_regime / scorecard /  │
│             picks / reflection / memory / sentiment / analysis …  │
├──────────────────────────────────────────────────────────────────┤
│  risk/ 闸门(唯一放行权威)   execution/ 券商抽象 + 模拟盘        │
│  screener/ 量化筛选   backtest/ 回测引擎   factors/ 因子挖掘      │
│  llm/ Gemini 客户端   data/ 行情/新闻/基本面源   store/ SQLite    │
└──────────────────────────────────────────────────────────────────┘
```

### 后端子模块职责

| 模块 | 职责 |
|---|---|
| `api/` | HTTP 路由。只读端点无门禁;改状态端点需 file-backed token。 |
| `services/` | 业务编排。**不做放行判断**,只把材料/委员会/闸门拼起来。 |
| `risk/` | RiskGate + 各风控规则 + 熔断。**唯一决定订单能否成交的地方。** |
| `execution/` | Broker 抽象(`base.py`)、`PaperBroker`(模拟盘)、`FutuBroker`(富途,env 硬门)、`order_manager`(下单 choke point)。 |
| `screener/` | 确定性量化筛选(趋势/动量/量能),把 universe 收窄成候选。 |
| `backtest/` | 回测引擎 + regime 叠加实验。 |
| `factors/` | 自主因子挖掘(只选结构化参数,绝不执行 LLM 代码)。 |
| `llm/` | Gemini 客户端;`generate_json` 失败一律 fail-safe 返回 None。 |
| `data/` | 行情(yfinance,带缓存)、新闻(finnhub)、基本面(EDGAR)。 |
| `store/` | SQLAlchemy/SQLite;`db.py` 含幂等补列迁移守卫。 |
| `mcp/` | FastMCP 工具层——**仅只读/建议工具,无下单/批准工具**。 |
| `watchdog/` | 熔断/健康监控。 |

---

## 3. 核心数据流:一次决策的一生

以 `full_auto` 每日交易循环(`trade_cycle_service.run_trade_cycle`)为例:

```
1. 抓行情           fetch_bars(universe)               [data/, 带缓存]
2. 量化筛选         run_screen_on_bars → top_n 候选     [screener/]
3. 容量预过滤       跳过"当日已不可能买入"的候选         [省 LLM;绝不放行/建单]
4. 大盘 regime      get_regime(SPY vs 200SMA) 算一次     [market_regime_service]
5. 逐候选:
   a. 组材料        get_stock_briefing(bars+news+基本面) [briefing_service]
   b. 记忆上下文    get_committee_context(该票历史+知识) [memory_service, 建议性]
   c. 委员会        run_committee(gemini, 宏观背景)       [committee_service]  ← 唯一 LLM 调用
   d. 服务端算股数  _size_shares(equity × cap ÷ price)   [绝不采信 LLM 的 shares]
   e. 提交决策      submit_decision → 落库 → 分流         [decision_service]
        └ advisory/hold → 只记录,不建单
        └ semi_auto     → PENDING_CONFIRMATION(等人工在 UI 批准)
        └ full_auto     → 过 RiskGate → 通过则 APPROVED → PaperBroker.submit
   f. 逐标的撮合    settle_open(restrict_to={symbol})     [execution/,只碰当前标的]
6. 轮末扫尾撮合     处理遗留 SUBMITTED 单
7. 复盘            reflect_on_closed_trades → 写记忆      [reflection_service, 建议性]
```

**安全不变量贯穿全流程**:
- 第 4/5b/7 步产出的都是**建议性上下文**(regime、记忆、复盘)——只喂进 LLM prompt,绝不进闸门/下单路径。
- 第 5d 步的股数由服务端从账户权益算出;LLM 的委员会草案里根本没有 shares 字段。
- 第 5e 步是唯一的下单 choke point;`as_of` 与价格一律服务端派生,payload 无通道(防伪造未来日期绕过熔断/冷却)。

---

## 4. 决策核心:量化筛选 + LLM 委员会

### 量化筛选(screener/)
确定性打分:趋势(SMA 排列)、动量、量能。输出按分数排序的候选。**无 LLM、可回测。**

### 四角色委员会(committee_service)
单只标的一次 Gemini 调用,产出结构化裁决草案:
- 四角色:**技术面 / 基本面 / 情绪面 / 空头**;
- **主席**综合裁决,且 `bear_rebuttal` 必须显式回应空头;
- 输出 `{committee, chair, action, confidence}`,全部经 clamp(action 落在合法集合并服从持仓规则、confidence 夹到 [0,1]、文本截断)。

**校准(2026-07)**:回放评测发现委员会 96.7% 说买、置信度几乎恒为 0.85(几乎没信息量)。prompt 校准后要求:候选是预筛过的→"走势强"是基线不是买入理由、hold 合法、confidence 用满区间、空头有真实分量。跨区间验证:买入率牛市 63%→熊市 20%(委员会确实读市场环境),置信度 stdev 0.05→0.126。详见 [PROGRESS.md](PROGRESS.md)。

### 宏观感知
committee 的 prompt 里带一句大盘 regime 背景(SPY vs 200 日均线,risk-on/off),整轮只算一次复用。advisory,不碰闸门。

---

## 5. 安全模型(安全红线)

**这是本项目最重要的部分。以下不变量不可削弱,均有测试守卫。**

| # | 红线 | 强制方式 |
|---|---|---|
| 1 | LLM 只建议,唯一放行权威是 RiskGate | `submit_decision → handle_decision → _gate_check`;每笔非 hold 的 live 决策必过闸门。已由资金路径审查逐条核验。 |
| 2 | 股数永远服务端算,不信任 LLM 数字 | trade_cycle 路径 `_size_shares` 服务端算;即便调用方传天量 shares,买入超限被闸门拒、卖出被 `min(order, held)` 封顶(有遏制性回归测试钉死)。 |
| 3 | mode 唯一真相在 DB | payload.mode 一律剥离;`full_auto` 需二次确认;未知 mode fail-safe 到 advisory。 |
| 4 | 无转账/出金 | 全树关键词守卫测试;资金只在现金↔持仓间流转;MCP 无下单/批准工具。 |
| 5 | `as_of`/价格服务端派生 | 闸门用的日期由服务端时钟经 `et_trading_day` 派生,绝不用 payload 的(可伪造未来日绕过熔断/冷却/查重)。 |
| 6 | 熔断 + stale-quote fail-safe | 日亏熔断持久化;某持仓报价缺失→权益不可信→买单一律拒(仅允许卖出)。 |
| 7 | 接实盘硬门 | 富途 REAL 需 env `STOCKAGENT_FUTU_ALLOW_REAL` + 解锁密码;默认模拟盘;agent 绝不自动动真钱。 |
| 8 | 网络面 | 后端仅绑 127.0.0.1;改状态接口需 file-backed token(防 CSRF);token 不进浏览器(服务端代理注入);LLM 输出/外部新闻 clamp + 注入定界。 |

风控规则(risk/rules.py):单票市值上限、总仓上限、单日新开仓上限、冷却期、stale-quote。**闸门 AND 短路(一条拒即拒),规则异常 fail-closed(安全侧)。**

---

## 6. 记忆 / 知识系统

- **知识库**(memory):结构化条目(insight / factor / trade_review / market_note)。委员会决策时把相关知识 + 该票历史决策作**建议性上下文**读入。
- **交易复盘**:平仓自动算已实现盈亏(均价法),生成 post-mortem 写回记忆。
- **业绩追踪 + 记分卡**:把复盘聚合成胜率/累计盈亏;决策记分卡量决策的"形状"(动作分布、置信度分布),前瞻收益记分卡量决策"对不对"(按 action×置信度分桶的实际收益 + 按决策天数做显著性检验)。
- **自主因子挖掘**:LLM 只从固定因子目录选**结构化参数**,每个提案自动两窗口回测,只有稳健改善的才记为 validated——**证据门槛阻止 agent 相信没验证的因子**。

**安全性质**:以上产出全是建议性上下文,绝不碰闸门/下单路径(有隔离测试:order/gate/risk 模块不 import 任何建议性上下文模块)。

---

## 7. 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.12、FastAPI、FastMCP、SQLAlchemy + SQLite、uv(依赖/运行) |
| LLM | Google Gemini(`llm/gemini.py`);评测用的独立 judge/gen 见 scripts |
| 前端 | Next.js(App Router)、TypeScript、Tailwind、lightweight-charts |
| 数据 | yfinance(行情,带 parquet 缓存)、finnhub(新闻)、SEC EDGAR(基本面) |
| 券商 | 模拟盘自建 PaperBroker;实盘富途 OpenD 适配器(默认关闭) |
| 测试 | pytest(785 离线用例;联网用例 `-m network`) |

---

## 8. 本地运行

```bash
./run.sh          # 后端 :8000(仅本机)+ 前端 :3000
# 打开 http://localhost:3000
```
需要 `uv`(Python 3.12)、Node.js。LLM 功能需 `backend/.env` 设 `STOCKAGENT_GEMINI_API_KEY`(git-ignored)。
测试:`cd backend && uv run pytest -q`。

> 运维注意:uvicorn 不热重载——改后端代码后需重启进程才生效;前端 dev server 运行时不要跑 `npm run build` 或清 `.next/`(会把运行中的 dev server 弄成 500,重启即恢复)。
