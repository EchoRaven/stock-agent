# 定时任务定义(M2,两条)

```
CRON_TZ=Asia/Shanghai
```

下面所有 cron 表达式均为**北京时间(Asia/Shanghai)**,调度器必须以该 TZ 求值
(而非宿主机 / UTC 默认时区),否则两条任务的触发时刻、以及下面"盘后日报"
里 `date -d 'yesterday'` 的取值都会整体偏移。美股常规盘为美东 9:30-16:00;
夏令时切换时(3 月/11 月)需要人工核对一次。

## 1. 盘前分析(工作日,北京时间 21:00 ≈ 美东 9:00 夏令时)

- cron 表达式:`0 21 * * 1-5`
- 动作:唤起 agent,prompt:"执行 trading skill 的每日盘前流程"
  (即 run_screener → 逐候选 get_stock_briefing → 委员会 → submit_decision)。

## 2. 盘后日报(周二至周六,北京时间 05:00 ≈ 美东收盘后)

- cron 表达式:`0 5 * * 2-6`
- 动作:运行后端命令生成日报,并把输出的 markdown 推送到用户渠道:

```bash
cd /data1/common/haibotong/stock-agent/backend && \
  .venv/bin/python -m app.cli report --date "$(TZ=Asia/Shanghai date -d 'yesterday' +%F)"
```

- **必须显式传 `--date`,不能依赖默认的 `dt.date.today()`**:盘前分析(任务 1)在
  北京时间前一天 21:00 运行并把当天的 signals/decisions 落库,而盘后日报在
  次日 05:00 运行——此时"今天"已经翻篇,`today()` 查询的日期没有任何数据行,
  日报会天天空。盘前落库那天相对盘后运行时刻正是"昨天"(Asia/Shanghai),
  所以显式传 `date -d 'yesterday'`(同样以 Asia/Shanghai 求值)取回盘前写入的那一天。
- 日报同时落库(reports 表)并写 `reports/daily_YYYYMMDD.md`,渠道推送失败可从文件补发。

## 失败告警(M2 简化)

任一条 cron 连续失败时人工介入;watchdog 自动降级是 M3(全自动模式)范围。
