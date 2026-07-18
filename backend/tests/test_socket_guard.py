import socket

import pytest

from tests.conftest import NetworkBlockedError


def test_tcp_connect_is_blocked_by_default():
    # 192.0.2.1 是 TEST-NET 保留地址;屏障应在触网前直接抛错
    with pytest.raises(NetworkBlockedError):
        socket.create_connection(("192.0.2.1", 80), timeout=0.1)


def test_unix_socket_still_allowed(tmp_path):
    # 只拦 AF_INET/AF_INET6;本机 AF_UNIX 不受影响
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(tmp_path / "s.sock"))
    server.listen(1)
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.connect(str(tmp_path / "s.sock"))
    client.close()
    server.close()
