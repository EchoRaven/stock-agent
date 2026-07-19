"""安全红线守卫:系统内永不存在转账/出金/提现方法。有人加了,这里必须红。"""
import re
from pathlib import Path

from app.execution.base import Broker
from app.execution.paper import PaperBroker

APP_DIR = Path(__file__).resolve().parents[2] / "app"
EXECUTION_DIR = APP_DIR / "execution"
FORBIDDEN = ("transfer", "withdraw", "deposit", "payout", "wire_",
             "move_funds", "send_funds", "sweep", "liquidate", "remit",
             "disburse", "move_money")
DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_0-9]+)", re.MULTILINE)

ALLOWED_BROKER_PUBLIC_METHODS = {"submit", "process_fills"}


def _concrete_broker_subclasses():
    """app/execution/ 下所有 Broker 具体子类(排除抽象基类本身)。"""
    import importlib
    import inspect

    subclasses = []
    for path in sorted(EXECUTION_DIR.glob("*.py")):
        if path.stem in ("__init__", "base"):
            continue
        module = importlib.import_module(f"app.execution.{path.stem}")
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, Broker) and obj is not Broker and not inspect.isabstract(obj):
                subclasses.append(obj)
    return subclasses


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


def test_broker_subclasses_expose_only_submit_and_process_fills():
    """防"偷渡"红线:未来加的 Broker 方法只要不叫 submit/process_fills 就必须变红——
    不依赖关键字匹配,任何新增公开方法(哪怕名字绕开了 FORBIDDEN 列表)都会被抓到。
    """
    subclasses = {PaperBroker, *_concrete_broker_subclasses()}
    assert PaperBroker in subclasses
    for cls in subclasses:
        public = {n for n in vars(cls) if not n.startswith("_")
                  and callable(getattr(cls, n))}
        assert public == ALLOWED_BROKER_PUBLIC_METHODS, \
            f"{cls.__name__} exposes unexpected public methods: {public}"
