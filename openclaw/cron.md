# 定时任务定义(M2,两条)

时区注意:美股常规盘 美东 9:30-16:00;下面用北京时间(Asia/Shanghai)表述,
夏令时切换时(3 月/11 月)需要人工核对一次。

## 1. 盘前分析(工作日,北京时间 21:00 ≈ 美东 9:00 夏令时)

- cron 表达式:`0 21 * * 1-5`
- 动作:唤起 agent,prompt:"执行 trading skill 的每日盘前流程"
  (即 run_screener → 逐候选 get_stock_briefing → 委员会 → submit_decision)。

## 2. 盘后日报(周二至周六,北京时间 05:00 ≈ 美东收盘后)

- cron 表达式:`0 5 * * 2-6`
- 动作:运行后端命令生成日报,并把输出的 markdown 推送到用户渠道:

```bash
cd /data1/common/haibotong/stock-agent/backend && .venv/bin/python -m app.cli report
```

- 日报同时落库(reports 表)并写 `reports/daily_YYYYMMDD.md`,渠道推送失败可从文件补发。

## 失败告警(M2 简化)

任一条 cron 连续失败时人工介入;watchdog 自动降级是 M3(全自动模式)范围。
