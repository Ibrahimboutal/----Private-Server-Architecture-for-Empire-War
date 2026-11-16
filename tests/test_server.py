"""Synthetic end-to-end tests using ephemeral 127.0.0.1 listeners only."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
import struct
from types import SimpleNamespace
from typing import AsyncIterator
from uuid import UUID

import pytest
import pytest_asyncio

from empire_war_private_server.auth.session_manager import SessionManager
from empire_war_private_server.config import ServerConfig
from empire_war_private_server.economy.resource_engine import CityResource, ResourceEngine
from empire_war_private_server import main as main_module
from empire_war_private_server.main import GameServer
from empire_war_private_server.protocol.frame_parser import Frame, FrameCodec
from empire_war_private_server.protocol.packet_router import PacketRouter

CODEC = FrameCodec()
# Authentication hashes and disk flushes vary with host load. These ordinary
# response deadlines are separate from the deliberately short idle-timeout test.
RESPONSE_TIMEOUT = 10
LOGIN = {"type": "User", "action": "doLogin", "passport": "fixture-local", "password": "fixture-password"}
SYNC = {"type": "City", "action": "synchCityResource", "cityId": 1}


@dataclass
class RunningServer:
    server: GameServer
    sessions: SessionManager
    engine: ResourceEngine
    port: int


@pytest_asyncio.fixture
async def local_server(tmp_path: Path) -> AsyncIterator[RunningServer]:
    config = ServerConfig(
        database=tmp_path / "state.db", audit_database=tmp_path / "audit.db",
        synthetic_passport=LOGIN["passport"], synthetic_password=LOGIN["password"],
        read_timeout_seconds=10,
    )
    engine = ResourceEngine(config.database, config.audit_database)
    await engine.initialize()
    await engine.seed_synthetic_user(
        config.synthetic_passport, config.synthetic_password,
        CityResource(cid=1, food=1000.0, wood=2000.0, iron=3000.0, gold=4000.0, people=50),
    )
    sessions = SessionManager(config.session_ttl_seconds, config.max_connections)
    server = GameServer(config, PacketRouter(engine, sessions))
    port = await server.start(port=0)
    try:
        yield RunningServer(server, sessions, engine, port)
    finally:
        await server.close()


@asynccontextmanager
async def client(port: int) -> AsyncIterator[tuple[asyncio.StreamReader, asyncio.StreamWriter]]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        yield reader, writer
    finally:
        writer.close()
        await writer.wait_closed()


async def exchange(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, payload: dict,
                   *, mode: int = 4) -> Frame:
    writer.write(CODEC.encode_frame(mode, payload))
    await writer.drain()
    result = await CODEC.read_frame(reader, timeout=RESPONSE_TIMEOUT)
    assert result is not None
    return result


async def expect_eof(reader: asyncio.StreamReader) -> None:
    async with asyncio.timeout(RESPONSE_TIMEOUT):
        assert await reader.read() == b""


@pytest.mark.asyncio
async def test_shutdown_cancels_active_clients_before_waiting(local_server: RunningServer) -> None:
    local_server.server.config = replace(local_server.server.config, read_timeout_seconds=30)
    async with client(local_server.port) as (reader, writer):
        await exchange(reader, writer, LOGIN)
        async with asyncio.timeout(5):
            await local_server.server.close()
        await expect_eof(reader)
        assert not local_server.server._clients


@pytest.mark.asyncio
async def test_login_compressed_response_and_resource_sync(local_server: RunningServer) -> None:
    async with client(local_server.port) as (reader, writer):
        login = await exchange(reader, writer, LOGIN)
        assert login.packet_type == 8  # Actual synthetic login exceeds 4,096 bytes.
        assert login.plaintext_length > 4096
        assert login.payload["ret"] == 0
        assert login.payload["type"] == "User"
        assert login.payload["action"] == "doLogin"
        session_id = login.payload["gameUUID"]
        assert UUID(session_id).version == 4
        assert local_server.sessions.get(session_id) is not None
        assert login.payload["user"]["name"] == "Research User"
        assert login.payload["user"]["passport"] == LOGIN["passport"]
        assert login.payload["user"]["ip"] == "127.0.0.1"
        assert login.payload["city"]["cid"] == 1
        assert login.payload["memCityResource"]["food"] == 1000.0
        assert login.payload["cfgSoldier"]
        response = await exchange(reader, writer, SYNC, mode=8)
        assert response.packet_type == 4
        assert response.payload["ret"] == 0
        assert response.payload["cityRes"] == login.payload["memCityResource"]
        assert "memCityResource" not in response.payload
        assert "password" not in login.payload["user"]


@pytest.mark.asyncio
async def test_session_cannot_be_transferred_by_request_fields(local_server: RunningServer) -> None:
    async with client(local_server.port) as (reader_a, writer_a), client(local_server.port) as (reader_b, writer_b):
        login_a = await exchange(reader_a, writer_a, LOGIN)
        token_a = login_a.payload["gameUUID"]
        for token_field in ("gameUUID", "session", "sessionId", "uuid"):
            rejected = await exchange(reader_b, writer_b, {**SYNC, token_field: token_a})
            assert rejected.payload["ret"] == 900101
        login_b = await exchange(reader_b, writer_b, LOGIN)
        assert login_b.payload["gameUUID"] != token_a
        assert (await exchange(reader_a, writer_a, SYNC)).payload["ret"] == 0
        assert (await exchange(reader_b, writer_b, SYNC)).payload["ret"] == 0


@pytest.mark.asyncio
async def test_invalid_login_revokes_previous_connection_session(local_server: RunningServer) -> None:
    async with client(local_server.port) as (reader, writer):
        accepted = await exchange(reader, writer, LOGIN)
        old_id = accepted.payload["gameUUID"]
        rejected = await exchange(reader, writer, {**LOGIN, "password": "incorrect"})
        assert rejected.payload["ret"] == 900101
        assert local_server.sessions.get(old_id) is None
        assert (await exchange(reader, writer, SYNC)).payload["ret"] == 900101
        restored = await exchange(reader, writer, LOGIN)
        assert restored.payload["ret"] == 0
        assert restored.payload["gameUUID"] != old_id


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_credentials", [
    {"passport": "unseeded-account"}, {"password": "incorrect"},
    {"passport": None}, {"password": 1234}, {"password": ""},
])
async def test_only_seeded_synthetic_credentials_are_accepted(
    local_server: RunningServer, bad_credentials: dict
) -> None:
    async with client(local_server.port) as (reader, writer):
        response = await exchange(reader, writer, {**LOGIN, **bad_credentials})
        assert response.payload["ret"] == 900101
        assert "gameUUID" not in response.payload
        assert (await exchange(reader, writer, SYNC)).payload["ret"] == 900101


@pytest.mark.asyncio
@pytest.mark.parametrize("city_id", [2, -1, 0, True, 1.0, "1", None])
async def test_sync_requires_owned_integer_city(local_server: RunningServer, city_id: object) -> None:
    async with client(local_server.port) as (reader, writer):
        await exchange(reader, writer, LOGIN)
        response = await exchange(reader, writer, {**SYNC, "cityId": city_id})
        assert response.payload["ret"] == 900102
        assert "cityRes" not in response.payload
        assert (await exchange(reader, writer, SYNC)).payload["ret"] == 0


@pytest.mark.asyncio
async def test_type_action_pairs_and_unimplemented_mutations(local_server: RunningServer) -> None:
    async with client(local_server.port) as (reader, writer):
        assert (await exchange(reader, writer, {**LOGIN, "type": "City"})).payload["ret"] == 900101
        await exchange(reader, writer, LOGIN)
        before = await local_server.engine.get_resources(1, 1)
        for payload in (
            {**SYNC, "type": "User"},
            {"type": "Troops", "action": "startDraftQueue", "sid": 1, "soldierCount": 10},
            {"type": "Market", "action": "sellToUser", "gold": 2147483647.0},
            {"type": "Building", "action": "upgradeImmediatelyByGoldWhenLackRes", "clientMoneyCost": 0},
        ):
            assert (await exchange(reader, writer, payload)).payload["ret"] == 900104
        assert await local_server.engine.get_resources(1, 1) == before


@pytest.mark.asyncio
async def test_fragmentation_coalescing_and_repeat_requests(local_server: RunningServer) -> None:
    async with client(local_server.port) as (reader, writer):
        wire = CODEC.encode_frame(4, LOGIN)
        for fragment in (wire[:2], wire[2:4], wire[4:9], wire[9:]):
            writer.write(fragment)
            await writer.drain()
            await asyncio.sleep(0)
        assert (await CODEC.read_frame(reader, timeout=RESPONSE_TIMEOUT)).payload["ret"] == 0
        repeated = CODEC.encode_frame(4, {**SYNC, "requestId": "same-synthetic-id"})
        writer.write(repeated + repeated)
        await writer.drain()
        for _ in range(2):
            assert (await CODEC.read_frame(reader, timeout=RESPONSE_TIMEOUT)).payload["ret"] == 0


@pytest.mark.asyncio
async def test_invalid_frame_disconnect_is_contained(local_server: RunningServer) -> None:
    malformed = [
        struct.pack(">BI", 4, local_server.server.config.max_frame_bytes + 1),
        struct.pack(">BI", 127, 0),
        struct.pack(">BI", 4, 1) + b"x",
    ]
    for wire in malformed:
        async with client(local_server.port) as (reader, writer):
            writer.write(wire)
            await writer.drain()
            await expect_eof(reader)
    async with client(local_server.port) as (reader, writer):
        assert (await exchange(reader, writer, LOGIN)).payload["ret"] == 0


@pytest.mark.asyncio
async def test_partial_frame_deadline_and_connection_capacity(local_server: RunningServer) -> None:
    bounded = GameServer(
        replace(local_server.server.config, max_connections=1, read_timeout_seconds=1),
        local_server.server.router,
    )
    port = await bounded.start(port=0)
    try:
        async with client(port) as (first_reader, first_writer):
            # Make admission deterministic before opening the excess connection.
            assert (await exchange(first_reader, first_writer, LOGIN)).payload["ret"] == 0
            async with client(port) as (extra_reader, _):
                await expect_eof(extra_reader)
            first_writer.write(b"\x04\x00")
            await first_writer.drain()
            await expect_eof(first_reader)  # Header never completes; read deadline closes it.
        async with client(port) as (reader, writer):
            assert (await exchange(reader, writer, LOGIN)).payload["ret"] == 0
    finally:
        await bounded.close()


@pytest.mark.asyncio
async def test_rate_limit_disconnect_and_shutdown_revoke_sessions(
    local_server: RunningServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A fixed per-connection clock makes this independent of machine speed.
    monkeypatch.setattr(main_module, "time", SimpleNamespace(monotonic=lambda: 100.0))
    async with client(local_server.port) as (reader, writer):
        login = await exchange(reader, writer, LOGIN)
        session_id = login.payload["gameUUID"]
        wire = CODEC.encode_frame(4, SYNC)
        writer.write(wire * local_server.server.config.requests_per_second)
        await writer.drain()
        for _ in range(local_server.server.config.requests_per_second - 1):
            assert (await CODEC.read_frame(reader, timeout=RESPONSE_TIMEOUT)).payload["ret"] == 0
        await expect_eof(reader)
        assert local_server.sessions.get(session_id) is None
    async with client(local_server.port) as (reader, writer):
        login = await exchange(reader, writer, LOGIN)
        session_id = login.payload["gameUUID"]
        await local_server.server.close()
        await expect_eof(reader)
        assert local_server.sessions.get(session_id) is None


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "localhost", "192.168.1.1", "127.0.0.2"])
def test_only_explicit_loopback_listener_is_allowed(host: str) -> None:
    with pytest.raises(ValueError, match="127.0.0.1"):
        ServerConfig(host=host)


def test_config_paths_and_unknown_fields(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("database: db/state.db\naudit_database: db/audit.db\nport: 9999\n", encoding="utf-8")
    loaded = ServerConfig.load(config)
    assert loaded.database == tmp_path / "db" / "state.db"
    assert loaded.audit_database == tmp_path / "db" / "audit.db"
    assert loaded.port == 9999
    config.write_text("typo_option: true\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown"):
        ServerConfig.load(config)
