"""安全红线守卫:系统内永不存在转账/出金/提现方法。有人加了,这里必须红。"""
import re
from pathlib import Path

from app.execution.base import Broker

APP_DIR = Path(__file__).resolve().parents[2] / "app"
FORBIDDEN = ("transfer", "withdraw", "deposit", "payout", "wire_",
             "move_funds", "send_funds")
DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_0-9]+)", re.MULTILINE)


def test_broker_interface_has_no_fund_egress():
    names = {n.lower() for n in dir(Broker)}
    hits = [n for n in names for bad in FORBIDDEN if bad in n]
    assert hits == []


def test_no_app_module_defines_fund_egress_functions():
    offenders = []
    for path in sorted(APP_DIR.rglob("*.py")):
        for name in DEF_RE.findall(path.read_text(encoding="utf-8")):
            if any(bad in name.lower() for bad in FORBIDDEN):
                offenders.append(f"{path.relative_to(APP_DIR)}:{name}")
    assert offenders == [], f"fund-egress function detected: {offenders}"
