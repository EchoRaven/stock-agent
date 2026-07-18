import datetime as dt
import json
import re

from app.report.daily import render_daily_report

# 只在未转义的 "|" 处切分(即前一字符不是反斜杠),模拟 markdown 表格解析器
# 识别真实列分隔符的方式:用来验证注入的 "|" 已被转义为字面量而非新增了一列。
_UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")


class FakeDecisionRow:
    def __init__(self, symbol, action, confidence, verdict):
        self.symbol = symbol
        self.action = action
        self.confidence = confidence
        self.payload_json = json.dumps({"chair": {"verdict": verdict}})


def test_verdict_with_pipe_and_newline_does_not_break_table_row():
    verdict = "risk | high\nwatch closely"
    row = FakeDecisionRow("AAPL", "buy", 0.8, verdict)
    text = render_daily_report(dt.date(2026, 7, 17), [], [row])

    data_lines = [line for line in text.splitlines() if line.startswith("| AAPL")]
    assert len(data_lines) == 1  # 换行未把该行拆成多行

    header_cols = _UNESCAPED_PIPE.split("| 代码 | 动作 | 置信度 | 主席裁决 |")
    data_cols = _UNESCAPED_PIPE.split(data_lines[0])
    assert len(data_cols) == len(header_cols)  # 列数与表头一致,注入的 | 未新增列

    assert "\\|" in data_lines[0]  # 原始 | 被转义为字面量
    assert "\n" not in data_lines[0].strip("\n")
