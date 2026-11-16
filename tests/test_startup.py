"""The real client's public version check precedes authenticated gameplay."""

import asyncio
import json
from pathlib import Path
import sqlite3

import pytest

from empire_war_private_server.auth.session_manager import ConnectionContext, SessionManager
from empire_war_private_server.config import ServerConfig
from empire_war_private_server.economy.resource_engine import CityResource, ResourceEngine
from empire_war_private_server.main import GameServer
from empire_war_private_server.protocol.frame_parser import FrameCodec
from empire_war_private_server.protocol.packet_router import PacketRouter

VERSION = {"type": "User", "action": "getServerVersion", "marketId": 0,
           "packageChannel": "FT", "phoneOs": "android"}
EXPECTED = {"type": "User", "action": "getServerVersion", "ret": 0, "version": 1719,
            "marketApkUrl": "market://details?id=com.feelingtouch.empirewaronline"}
LOGIN = {"type": "User", "action": "doLogin", "passport": "startup-fixture", "password": "local-only"}
SYNC = {"type": "City", "action": "synchCityResource", "cityId": 1}


@pytest.fixture
async def router(tmp_path):
    ledger = ResourceEngine(tmp_path / "state.db", tmp_path / "audit.db")
    await ledger.initialize()
    await ledger.seed_synthetic_user(LOGIN["passport"], LOGIN["password"], CityResource(food=100))
    return PacketRouter(ledger, SessionManager())


async def test_public_handshake_does_not_grant_access_or_mutate_state(router):
    context = ConnectionContext()
    before = await router.engine.get_resources(1, 1)
    assert (await router.dispatch(SYNC, context))["ret"] == 900101
    for _ in range(2):
        assert await router.dispatch(VERSION, context) == EXPECTED
        assert context.session_id is None and router.sessions._sessions == {}
        assert (await router.dispatch(SYNC, context))["ret"] == 900101
    assert await router.engine.get_resources(1, 1) == before
    async with router.engine._connection() as db:
        async with db.execute("SELECT action FROM audit.economy_events") as cur:
            assert len(await cur.fetchall()) == 1  # Synthetic account seeding only.


async def test_handshake_preserves_existing_session_and_expiry(router):
    context = ConnectionContext()
    assert (await router.dispatch(LOGIN, context))["ret"] == 0
    session = router.sessions.get(context.session_id)
    assert await router.dispatch(VERSION, context) == EXPECTED
    assert router.sessions.get(context.session_id) == session
    assert (await router.dispatch(SYNC, context))["ret"] == 0
    router.sessions.revoke(context.session_id)
    assert await router.dispatch(VERSION, context) == EXPECTED
    assert router.sessions.get(context.session_id) is None
    assert (await router.dispatch(SYNC, context))["ret"] == 900101


@pytest.mark.parametrize("change,ret", [
    ({"marketId": True}, 900103), ({"marketId": 1.0}, 900103),
    ({"marketId": 2**31}, 900103), ({"packageChannel": None}, 900103),
    ({"phoneOs": []}, 900103), ({"phoneOs": "android" * 10}, 900103),
    ({"marketId": 1}, 900105), ({"packageChannel": "unknown"}, 900105),
    ({"phoneOs": "ios"}, 900105), ({"type": "City"}, 900101),
])
async def test_invalid_or_unsupported_handshake_never_creates_session(router, change, ret):
    context = ConnectionContext()
    assert (await router.dispatch({**VERSION, **change}, context))["ret"] == ret
    assert context.session_id is None and not router.sessions._sessions


@pytest.mark.parametrize("mode", [4, 8])
async def test_version_login_sync_sequence_over_one_tcp_connection(router, mode):
    server = GameServer(ServerConfig(), router)
    port = await server.start(port=0)
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    codec = FrameCodec()
    try:
        # The first request matches the observed stock Android startup packet.
        for request, expected in ((VERSION, EXPECTED), (SYNC, {"ret": 900101}),
                                  (LOGIN, {"ret": 0}), (SYNC, {"ret": 0})):
            writer.write(codec.encode_frame(mode, request))
            await writer.drain()
            response = await codec.read_frame(reader, timeout=10)
            assert response is not None
            assert all(response.payload[key] == value for key, value in expected.items())
    finally:
        writer.close()
        await writer.wait_closed()
        await server.close()


def test_version_response_and_startup_order_match_original_captures():
    path = Path(__file__).resolve().parents[1] / "new-set-up/game_protocol_v2.db"
    if not path.is_file():
        pytest.skip("optional capture database not present")
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        db.execute("BEGIN")
        responses = db.execute("SELECT payload_json FROM captured_packets WHERE action_name='getServerVersion' AND direction='SERVER -> CLIENT'").fetchall()
        assert len(responses) >= 28
        assert all(json.loads(row[0]) == EXPECTED for row in responses)
        first = db.execute("SELECT action_name,direction FROM captured_packets WHERE id IN (1,2,3) ORDER BY id").fetchall()
        assert first == [("getServerVersion", "CLIENT -> SERVER"),
                         ("getServerVersion", "SERVER -> CLIENT"), ("doLogin", "CLIENT -> SERVER")]
