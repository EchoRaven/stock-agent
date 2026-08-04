# 文档索引 / Docs Index

stock-agent 的项目文档。**这里是团队文档的单一事实来源(single source of truth)**——随代码一起版本化,改代码时一并更新对应文档。

## 核心文档

| 文档 | 内容 |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | **技术架构 / 技术路线**:系统组成、一次决策的数据流、决策核心、安全模型(红线)、技术栈、本地运行。 |
| [ROADMAP.md](ROADMAP.md) | **技术路线图**:里程碑 M1–M9、已完成能力清单、进行中/计划、已知问题、路线图原则。 |
| [PROGRESS.md](PROGRESS.md) | **进度文档**:当前状态、评测结论(含证据强度)、我们修正的自身错误、迭代日志、安全审查结论。 |

## 策略实验报告(诚实的负面结果为主)

| 文档 | 内容 |
|---|---|
| [strategy_experiment_report.md](strategy_experiment_report.md) | 策略实验 |
| [regime_experiment_report.md](regime_experiment_report.md) | 大盘 regime 叠加实验 |
| [stoploss_experiment_report.md](stoploss_experiment_report.md) | 止损实验 |
| [universe_experiment_report.md](universe_experiment_report.md) / [universe_pit_report.md](universe_pit_report.md) | 股票池实验 / point-in-time |
| [news_sentiment.md](news_sentiment.md) | 新闻情绪 |
| [futu_setup.md](futu_setup.md) | 富途实盘接入(默认关闭,REAL 硬门) |

## 维护约定

- 文档随代码演进:改了架构/安全模型 → 更新 ARCHITECTURE;完成里程碑/新增已知问题 → 更新 ROADMAP;有新评测结论或重要迭代 → 更新 PROGRESS。
- 评测结论必须标注**证据强度**;不把弱证据(单一行情区间、带偏差)包装成结论。
- 顶层项目介绍见仓库根 [../README.md](../README.md)。
