import datetime as dt


def render_screen_report(scores, as_of: dt.date) -> str:
    lines = [f"# 每日筛选报告 {as_of.isoformat()}", ""]
    lines += ["| 排名 | 代码 | 总分 | 明细 |", "|---|---|---|---|"]
    for i, s in enumerate(scores, 1):
        parts = "; ".join(f"{name}={r.score:.2f}" for name, r in s.parts.items())
        lines.append(f"| {i} | {s.symbol} | {s.total:.3f} | {parts} |")
    lines.append("")
    for s in scores:
        lines.append(f"## {s.symbol}")
        for name, r in s.parts.items():
            lines.append(f"- **{name}** ({r.score:.2f}): {r.detail}")
        lines.append("")
    return "\n".join(lines)


def render_backtest_report(result, config) -> str:
    m = result.metrics
    return "\n".join(
        [
            f"# 回测报告 {config.start.isoformat()} ~ {config.end.isoformat()}",
            "",
            f"- 初始资金: {config.initial_cash:,.0f}",
            f"- 总收益: {m['total_return']:.2%}",
            f"- 最大回撤: {m['max_drawdown']:.2%}",
            f"- 夏普(年化): {m['sharpe']:.2f}",
            f"- 胜率: {m['win_rate']:.2%}",
            f"- 成交笔数: {int(m['num_fills'])}",
            "",
        ]
    )
