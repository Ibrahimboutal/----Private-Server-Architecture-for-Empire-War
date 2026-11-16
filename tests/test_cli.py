"""Exercise the real executable entry points, including their socket audit hook."""

from __future__ import annotations

import json
from pathlib import Path
import socket
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]


def test_server_entrypoint_and_smoke_client(tmp_path: Path) -> None:
    # A short-lived loopback socket selects a free local test port. If another
    # process claims it first, server startup fails visibly rather than targeting it.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = tmp_path / "config.yaml"
    config.write_text(json.dumps({
        "host": "127.0.0.1", "port": port,
        "database": "state.db", "audit_database": "audit.db",
        "synthetic_passport": "cli-fixture", "synthetic_password": "fixture-only",
    }), encoding="utf-8")
    log_path = tmp_path / "server.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "empire_war_private_server.main", "--config", str(config)],
            cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 20
            while "listening on" not in log_path.read_text(encoding="utf-8"):
                assert process.poll() is None, log_path.read_text(encoding="utf-8")
                assert time.monotonic() < deadline, "server startup deadline exceeded"
                time.sleep(0.05)
            result = subprocess.run(
                [sys.executable, "tools/local_smoke_client.py", "--config", str(config)],
                cwd=ROOT, capture_output=True, text=True, timeout=20,
            )
            assert result.returncode == 0, result.stderr
            assert "PASS:" in result.stdout
        finally:
            # Only the process created by this test is stopped.
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def test_installed_guard_blocks_external_socket_before_network_io() -> None:
    script = """
import socket
from empire_war_private_server.network_isolation import install_loopback_guard
install_loopback_guard()
with socket.socket() as client:
    try:
        client.connect(('192.0.2.1', 1))
    except PermissionError:
        print('blocked by audit hook')
    else:
        raise AssertionError('non-loopback connection was permitted')
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "blocked by audit hook"
