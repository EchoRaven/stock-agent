# 评测工具链 / Evaluation Harness

> 我们用来回答"这个 agent 到底行不行"的脚本 + 方法论。所有 M8/M9 的结论都出自这套工具。
> 配套:[ARCHITECTURE.md](ARCHITECTURE.md)、[ROADMAP.md](ROADMAP.md)、[PROGRESS.md](PROGRESS.md)。状态截至 2026-08。

---

## 0. 为什么单列一份文档

这套项目最花力气、也最值得复用的不是功能,而是**一套不自欺的评测纪律**。工具本身会骗人;我们踩过好几个"测量缺陷"(见 PROGRESS §3),最后沉淀出四条工具级护栏:**隔离、fail-safe 熔断、诚实门控、区间估计**。这份文档把四个脚本和这套方法论讲清楚,让下一个人(很可能是半年后的你)能直接复用、也能看懂每个数字的证据强度。

---

## 1. 方法论(四条纪律)

1. **测量先于优化**:改委员会/策略前,先能量出改动效果(记分卡 + 回放 A/B),否则就是拿单一行情过拟合。
2. **跨区间才算数**:任何"某类决策好/坏"的结论,至少跨一个**反向行情区间**验证。这条直接救回过"卖出反预测"的误判(那是上涨行情产物,见 PROGRESS §2.3)。
3. **诚实门控**:样本量按**独立观测数**(决策天数),不是按行数;`pending`(横期未到)/`unpriced`(无行情)绝不当 0;够样本才下结论,否则如实报"样本不足/不显著"。
4. **区间估计**:小样本上的漂亮点估计是陷阱(3/3≠确定性)。比例配 **Wilson 95% 区间**,只在区间**分离**时才认效应。

---

## 2. 四个脚本(都在 `backend/scripts/`,不进 pytest,需网络 + Gemini key)

| 脚本 | 回答什么 | 核心机制 |
|---|---|---|
| **replay_eval.py** | 委员会的**置信度能否预测收益**?决策的形状对不对? | 重放 screen→briefing→committee 写决策(**只决策,不成交**),接前瞻收益记分卡 |
| **replay_loop.py** | 学习闭环能否**通电**(产出平仓复盘)? | 隔离库里按历史推进**完整** `run_trade_cycle`(含撮合/平仓/`reflect` 写复盘) |
| **learning_ab.py** | 委员会读到自己的复盘后**行为是否真变**? | difference-in-differences(WITH vs WITHOUT memory)+ Wilson 区间 |
| **_health.py** | (护栏)防配额耗尽产污染数据 | Gemini 探活 + fail-safe 熔断,被 replay_* 复用 |

### replay_eval.py —— 置信度→收益 + 决策形状
```bash
uv run python -m scripts.replay_eval --dry-run                         # 只看计划/成本
uv run python -m scripts.replay_eval --dates 12 --top-k 5              # 近期一段
uv run python -m scripts.replay_eval --dates 8 --top-k 3 --end-date 2025-04-04   # 指定窗口(跨区间)
uv run python -m scripts.replay_eval --dates 12 --hold AAPL,JPM        # 让 sell 可测(否则被 clamp 成 hold)
uv run python -m scripts.replay_eval --report-only                    # 对已有库重出报告
```
- 写到独立库(`--db`,默认 `scripts/replay_eval.db`),**绝不碰线上 stockagent.db**;`mode="replay"`,不碰下单/闸门路径。
- 记分卡:动作分布、置信度分布、**前瞻收益按 action×置信度分桶 + 命中率**、置信度→收益的**按天数**保守 t 检验。
- `--hold` 才能测卖出(无持仓时 `_clamp_action` 把 sell 改写成 hold);`--end-date` 做跨区间。

### replay_loop.py —— 给学习闭环通电
```bash
uv run python -m scripts.replay_loop --dry-run
uv run python -m scripts.replay_loop --start 2025-03-10 --end 2025-04-11 --top-n 3
```
- 隔离库里按历史日期推进**完整** `run_trade_cycle`:持仓在波动行情里开→平,`reflect_on_closed_trades` 把平仓复盘写进记忆,委员会下轮读到 —— 这就是"通电"。
- 成本高:一次委员会 = 每评估标的一次 Gemini 调用(候选 + 持仓),一段几十天上百次调用。

### learning_ab.py —— 委员会到底学没学到
```bash
# memory 通道(默认):复盘埋在 memory_context 里
uv run python -m scripts.learning_ab --db <replay_loop库副本> --as-of 2025-05-19 --reps 4 --controls 6
# 加功率:合成注入亏损复盘到多只 treatment(不必真跑那么多交易)
uv run python -m scripts.learning_ab --db <副本> --inject NVDA,GOOGL,AMZN,TSLA --reps 4
# prominent 通道(M9 attempt#2):该票上次结果单独显眼摆 prompt 顶部
uv run python -m scripts.learning_ab --db <副本> --prominent --reps 4 --controls 6
```
- **difference-in-differences**:treatment(有亏损复盘的票)vs control(无复盘的票),各跑 WITH/WITHOUT memory,比较**买入率(比例)**。
- **Wilson 95% 区间**:只在 treatment 的 WITH/WITHOUT 区间**分离**时才认"复盘压低了买入";重叠 = 测不出(≠证否)。
- `--inject` 合成注入(格式与真复盘一致,加功率);`--prominent` 换独立显眼通道(A/B 两种喂法)。
- **评估日要选对**:risk-off 日委员会几乎全 hold → 买入率地板、测不出;要用委员会本会买的日子(bull),复盘才有"买可压"。

---

## 3. 护栏(踩过坑才加的)

- **隔离**:所有脚本写独立 `--db`,绝不碰线上库。replay_loop 虽然跑完整下单/撮合,但只在隔离库里、隔离库自己的 mode/账户。
- **fail-safe 熔断**(`_health.py`):重度回放会耗尽 Gemini 日配额 → 429 → 委员会全 fail-safe(hold/conf=0.0),一整轮变污染数据。`require_gemini()` 跑批前探活、429 就别开跑;`FailSafeGuard` 跑批中 fail-safe 率过高就熔断。**已在真实 429 下实测**,不再产污染库。
- **重跑前删污染库**:replay_* 会**跳过已存在决策的日期**,坏行(fail-safe)不删就被保留、重跑不干净 → `rm scripts/replay_*.db` 再跑。
- **绝不并发两个 Gemini 任务**(回放/cycle/picks/analyze 互斥),否则抢限流。
- **信任任何结果前先数 `confidence<=0.05` 占比**;超个位数% = 被 fail-safe 污染,作废。

---

## 4. 已知偏差(读任何数字前先看)

- **基本面非 point-in-time**:`EdgarFundamentalsProvider` 不吃 as_of,回放过去用的是**今天**的财报 → 真实未来函数,窗口越早越严重。
- **幸存者偏差**:universe 是今天的列表。
- **单一/少数行情区间**:一个窗口一个 regime,单窗口的行为结论几乎必然是区间产物(所以有纪律 2)。
- **无 memory_context**(replay_eval):与线上委员会略有差异。
- **合成复盘**(learning_ab --inject):格式与真复盘一致但非真实交易。

**因此:所有"agent 决策好/坏"的判断都是弱证据。** 跨区间验证 + 区间估计是把弱证据用诚实的方式呈现,不是把它变强。

---

## 5. 到目前为止的结论(证据强度已标注)

- **置信度→收益**:三区间干净够样本(39 买入/22 决策日/fail-safe 0%)→ **不显著**(r=0.16/0.06)。测了,现有证据里置信度不预测收益。
- **学习闭环通电**:replay_loop 实测产出平仓往返 → trade_review 进记忆。**机制✅**。
- **委员会是否从自己的复盘学到**:learning_ab(Wilson 区间)→ treatment WITH/WITHOUT 区间**重叠**,**测不出效应**。委员会响应通用记忆却不响应该票亏损复盘。
- **M9 attempt#2**(把该票上次结果显眼摆出)plumbing 已就绪、可测,**尚未验证也未接线上**(待跑 `--prominent` A/B)。

完整叙述见 [PROGRESS.md](PROGRESS.md);里程碑与下一步见 [ROADMAP.md](ROADMAP.md)。
