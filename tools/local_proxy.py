"""Run the existing ewo-proxy against the local emulator with rewrites disabled.

No ADB changes or official endpoints. Captures/state go to a separate runtime
directory; the Python socket guard prevents accidental external connections.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from empire_war_private_server.config import ServerConfig
from empire_war_private_server.network_isolation import install_loopback_guard


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    parser.add_argument("--listen-port", type=int, default=9999)
    parser.add_argument("--control-port", type=int, default=10001)
    args = parser.parse_args()
    config = ServerConfig.load(args.config)
    if len({args.listen_port, args.control_port, config.port}) != 3:
        parser.error("proxy, control and emulator ports must be distinct")
    if any(not 1 <= port <= 65535 for port in (args.listen_port, args.control_port)):
        parser.error("ports must be 1..65535")
    install_loopback_guard()
    path = ROOT / "new-set-up/enhanced_proxy_draft_reference.py"
    if not path.is_file():
        parser.error("existing ewo-proxy is missing from new-set-up")
    sys.path.insert(0, str(path.parent))
    # Apply the relay's recursive redactor to push tokens in local captures.
    from game_protocol import _PRIVATE_KEYS
    _PRIVATE_KEYS.update({"regid", "gcmregid"})
    spec = importlib.util.spec_from_file_location("ewo_local_proxy", path)
    assert spec and spec.loader
    proxy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proxy)
    runtime = config.database.parent / "proxy"
    runtime.mkdir(parents=True, exist_ok=True)
    raise SystemExit(proxy.main([
        "--listen-host", "127.0.0.1", "--listen-port", str(args.listen_port),
        "--control-port", str(args.control_port), "--server-host", "127.0.0.1",
        "--server-port", str(config.port), "--db", str(runtime / "local_captures.db"),
        "--state-file", str(runtime / "proxy_state.json"), "--log-file", str(runtime / "proxy.log"),
        "--disable-resource-rewrite", "--quiet-payloads",
    ]))


if __name__ == "__main__":
    main()
