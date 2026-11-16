"""Unit checks of the process-local socket audit policy, without sending packets."""

import pytest

from empire_war_private_server.config import ServerConfig
from empire_war_private_server.network_isolation import _check


@pytest.mark.parametrize("event,args", [
    ("socket.connect", (None, ("192.0.2.1", 80))),
    ("socket.bind", (None, ("0.0.0.0", 1024))),
    ("socket.connect", (None, ("::1", 80, 0, 0))),
    ("socket.getaddrinfo", ("example.test", 80, 0, 0, 0)),
    ("socket.sendto", (None, b"test", ("192.0.2.1", 53))),
    ("socket.gethostbyname", ("example.test",)),
    ("socket.gethostbyaddr", ("192.0.2.1",)),
])
def test_external_network_operations_rejected(event, args):
    with pytest.raises(PermissionError):
        _check(event, args)


def test_loopback_permitted():
    _check("socket.bind", (None, ("127.0.0.1", 0)))
    _check("socket.connect", (None, ("127.0.0.1", 1024)))
    _check("socket.getaddrinfo", ("127.0.0.1", 1024, 0, 0, 0))
    _check("unrelated.event", ())


def test_all_config_paths_relative_to_config_file(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("port: 1024\n")
    config = ServerConfig.load(path)
    assert config.database == tmp_path / "runtime/state.db"
    assert config.audit_database == tmp_path / "runtime/audit.db"
