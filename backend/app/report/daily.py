import datetime as dt
import json


def _cell(text: str) -> str:
    """LLM/未受控自由文本进入 markdown 表格单元格前的转义:
    "|" 会被误判为新增一列,换行会把一行拆成多行,都需要先处理掉。"""
    return str(text or "").replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _decision_line(row) -> str:
    payload = json.loads(row.payload_json)
    verdict = payload.get("chair", {}).get("verdict", "")
    return f"| {row.symbol} | {row.action} | {row.confidence:.2f} | {_cell(verdict)} |"


def render_daily_report(report_date: dt.date, signals: list, decisions: list) -> str:
    lines = [f"# 每日交易日报 {report_date.isoformat()}", "", "## 筛选快照", ""]
    if signals:
        lines += ["| 排名 | 代码 | 总分 |", "|---|---|---|"]
        lines += [f"| {s.rank} | {s.symbol} | {s.total:.3f} |" for s in signals]
    else:
        lines.append("(当日无筛选快照)")
    lines += ["", "## 委员会决定(建议模式,未生成订单)", ""]
    if decisions:
        lines += ["| 代码 | 动作 | 置信度 | 主席裁决 |", "|---|---|---|---|"]
        lines += [_decision_line(d) for d in decisions]
    else:
        lines.append("(当日无决定)")
    lines.append("")
    return "\n".join(lines)
