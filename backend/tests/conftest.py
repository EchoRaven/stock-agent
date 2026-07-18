"""全局测试护栏:非 network 标记的测试一律禁止对外 TCP 连接(防意外联网)。"""
import socket

import pytest

_REAL_CONNECT = socket.socket.connect


class NetworkBlockedError(RuntimeError):
    """单元测试发起了真实网络连接(应 mock,或标 @pytest.mark.network)。"""


@pytest.fixture(autouse=True)
def _block_network(request, monkeypatch):
    if request.node.get_closest_marker("network"):
        yield
        return

    def guarded_connect(self, address, *args, **kwargs):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise NetworkBlockedError(
                f"unit test attempted TCP connect to {address!r}; "
                "mock it or mark the test with @pytest.mark.network")
        return _REAL_CONNECT(self, address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    yield
