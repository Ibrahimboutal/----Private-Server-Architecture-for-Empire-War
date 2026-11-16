"""Existing ewo-proxy interoperability against the synthetic loopback server.

No existing capture/state/log file is opened for writing. The upstream, game
listener, and control listener all use explicit 127.0.0.1 and ephemeral ports.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sqlite3
import struct
import threading
from typing import Any

import pytest

from empire_war_private_server.auth.session_manager import SessionManager
from empire_war_private_server.config import ServerConfig
from empire_war_private_server.economy.resource_engine import CityResource, ResourceEngine
from empire_war_private_server.main import GameServer
from empire_war_private_server.protocol.frame_parser import FrameCodec
from empire_war_private_server.protocol.packet_router import PacketRouter


class RecordingCodec(FrameCodec):
    """Retain only this test's synthetic outgoing wire for exact comparisons."""

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[bytes] = []

    def encode_payload(self, payload: dict[str, Any]) -> bytes:
        wire = super().encode_payload(payload)
        self.sent.append(wire)
        return wire


async def read_wire(reader: asyncio.StreamReader) -> bytes:
    async with asyncio.timeout(5):
        header = await reader.readexactly(5)
        length = struct.unpack(">I", header[1:])[0]
        assert length <= 1_048_576
        return header + await reader.readexactly(length)


def load_relay(monkeypatch: pytest.MonkeyPatch) -> Any:
    directory = Path(__file__).resolve().parents[1] / "new-set-up"
    source = directory / "enhanced_proxy_draft_reference.py"
    if not source.exists():
        pytest.skip("optional local ewo-proxy source is not present")
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location("local_proxy_compatibility_test", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_original_proxy_preserves_synthetic_server_wire_with_rewrite_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    relay = load_relay(monkeypatch)
    config = ServerConfig(database=tmp_path / "state.db", audit_database=tmp_path / "audit.db")
    engine = ResourceEngine(config.database, config.audit_database)
    await engine.initialize()
    passport, password = "proxy-fixture-user", "proxy-fixture-secret"
    resources = CityResource(cid=1, food=125.0, gold=250.0, wood=375.0, iron=500.0, rock=625.0, people=20)
    await engine.seed_synthetic_user(passport, password, resources)
    server = GameServer(config, PacketRouter(engine, SessionManager()))
    recording = RecordingCodec()
    server.codec = recording
    upstream_port = await server.start(port=0)
    proxy = None
    thread = None
    failures: list[str] = []
    writer = None
    try:
        args = relay.parse_args([
            "--listen-host", "127.0.0.1", "--listen-port", "9999",
            "--control-port", "10001", "--server-host", "127.0.0.1",
            "--server-port", str(upstream_port), "--disable-resource-rewrite",
            "--db", str(tmp_path / "capture.db"),
            "--state-file", str(tmp_path / "proxy-state.json"),
            "--log-file", str(tmp_path / "proxy.log"),
        ])
        # parse_args validates production ports; Proxy itself supports OS-assigned
        # ports, avoiding a race-prone search for currently unused fixed ports.
        args.listen_port = args.control_port = 0
        assert args.server_host == args.listen_host == "127.0.0.1"
        assert args.disable_resource_rewrite is True
        assert not args.store_raw and not args.enable_injection and not args.isolate_market_tests
        proxy = relay.Proxy(args)

        def run_proxy() -> None:
            try:
                proxy.run()
            except Exception as exc:
                failures.append(type(exc).__name__)

        thread = threading.Thread(target=run_proxy, name="synthetic-compatibility-proxy", daemon=True)
        thread.start()
        async with asyncio.timeout(10):
            while not Path(args.state_file).exists():
                assert not failures, "local proxy startup failed"
                await asyncio.sleep(0.01)
        assert len(proxy.listeners) == 2
        assert all(listener.getsockname()[0] == "127.0.0.1" for listener in proxy.listeners)
        proxy_port = proxy.listeners[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
        codec = FrameCodec()
        requests = [
            {"type": "User", "action": "doLogin", "passport": passport, "password": password},
            {"type": "City", "action": "synchCityResource", "cityId": 1},
        ]
        for index, request in enumerate(requests):
            outgoing = codec.encode_frame(4, request)
            # The relay must preserve framing even when TCP fragments the header.
            for fragment in (outgoing[:2], outgoing[2:4], outgoing[4:]):
                writer.write(fragment)
                await writer.drain()
            incoming = await read_wire(reader)
            assert incoming == recording.sent[index]
            decoded = codec.decode_frame(incoming)
            assert decoded.payload["ret"] == 0
            if index == 0:
                assert decoded.packet_type == 8
                assert decoded.payload["memCityResource"] == resources.to_wire()
            else:
                assert decoded.packet_type == 4
                assert decoded.payload["cityRes"] == resources.to_wire()
                assert decoded.payload["cityRes"]["food"] == 125.0
        assert len(recording.sent) == 2
        writer.close()
        await writer.wait_closed()
        writer = None
    finally:
        if writer is not None:
            writer.close()
            await writer.wait_closed()
        if proxy is not None:
            proxy.stopping.set()
        if thread is not None:
            await asyncio.to_thread(thread.join, 15)
            assert not thread.is_alive(), "local proxy did not stop"
        await server.close()
    assert not failures, "local proxy thread failed"
    assert not (tmp_path / "proxy-state.json").exists()

    def check_capture() -> None:
        with sqlite3.connect((tmp_path / "capture.db").as_uri() + "?mode=ro", uri=True) as database:
            rows = database.execute(
                "SELECT action_name,payload_json,payload_hex,raw_captured FROM captured_packets"
            ).fetchall()
            assert len(rows) == 4
            assert sorted(row[0] for row in rows) == ["doLogin", "doLogin", "synchCityResource", "synchCityResource"]
            assert all(row[2] is None and row[3] == 0 for row in rows)
            assert all(password not in (row[1] or "") for row in rows)
            assert database.execute("SELECT count(*) FROM injection_log").fetchone()[0] == 0

    await asyncio.to_thread(check_capture)
