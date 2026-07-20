"""安全红线:agent 知识库(app/store/repos/memory_repo.py,
app/services/memory_service.py)是 ADVISORY CONTEXT ONLY——只喂进委员会
prompt 的一段说明性文本,绝不能被下单/风控路径读取或依赖。这里用源码静态
扫描 + 运行期依赖图两条腿确认:唯一下单 choke point
(app/execution/order_manager.py)与整个风控闸门(app/risk/)都不导入
memory_repo/memory_service,即便未来有人手滑加了 import,这两个测试也会红。
"""
import importlib
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent / "app"

GATE_AND_ORDER_MODULES = [
    "app/execution/order_manager.py",
    "app/risk/gate.py",
    "app/risk/rules.py",
    "app/risk/circuit_breaker.py",
]

FORBIDDEN_NEEDLES = ("memory_repo", "memory_service", "MemoryEntryRow")


def test_order_and_risk_source_never_mentions_memory_module():
    for rel_path in GATE_AND_ORDER_MODULES:
        source = (APP_ROOT.parent / rel_path).read_text(encoding="utf-8")
        for needle in FORBIDDEN_NEEDLES:
            assert needle not in source, (
                f"{rel_path} 引用了 {needle}——memory 必须是 ADVISORY CONTEXT ONLY,"
                "绝不能被下单/风控路径依赖"
            )


def test_order_manager_runtime_import_graph_excludes_memory():
    """比纯文本扫描更硬的一道:实际 import order_manager 后检查它已加载的模块
    依赖里没有 memory_repo/memory_service(防止间接 import 绕过文本扫描)。"""
    before = set(sys.modules)
    module = importlib.import_module("app.execution.order_manager")
    importlib.reload(module)  # 确保这次 import 触发的依赖被计入 sys.modules 的新增集合
    after = set(sys.modules)
    newly_loaded = after - before
    assert not any("memory" in name.lower() for name in newly_loaded)


def test_risk_gate_runtime_import_graph_excludes_memory():
    before = set(sys.modules)
    module = importlib.import_module("app.risk.gate")
    importlib.reload(module)
    after = set(sys.modules)
    newly_loaded = after - before
    assert not any("memory" in name.lower() for name in newly_loaded)
