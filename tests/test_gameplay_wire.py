"""Phase 2 through actual binary TCP frames, with no client-declared prices."""

import asyncio
from contextlib import asynccontextmanager
import time

import pytest

from empire_war_private_server.auth.session_manager import SessionManager
from empire_war_private_server.config import ServerConfig
from empire_war_private_server.economy.gameplay_engine import GameplayEngine
from empire_war_private_server.economy.resource_engine import CityResource, ResourceEngine
from empire_war_private_server.main import GameServer
from empire_war_private_server.protocol.frame_parser import FrameCodec
from empire_war_private_server.protocol.packet_router import PacketRouter

LOGIN = {"type": "User", "action": "doLogin", "passport": "wire-fixture", "password": "fixture-only"}
CODEC = FrameCodec()


@pytest.fixture
async def server(tmp_path):
    ledger = ResourceEngine(tmp_path / "state.db", tmp_path / "audit.db")
    await ledger.initialize()
    await ledger.seed_synthetic_user(LOGIN["passport"], LOGIN["password"], CityResource(
        food=10000, wood=10000, iron=10000, rock=10000, gold=10000, people=1000))
    clock = [1_800_000_000]
    gameplay = GameplayEngine(ledger, clock=lambda: clock[0])
    await gameplay.seed_city(1, 1)
    router = PacketRouter(ledger, SessionManager(), gameplay)
    server = GameServer(ServerConfig(), router)
    port = await server.start(port=0)
    try:
        yield server, port, clock
    finally:
        await server.close()


@asynccontextmanager
async def connected(port):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    async def exchange(payload):
        writer.write(CODEC.encode_payload(payload))
        await writer.drain()
        frame = await CODEC.read_frame(reader, timeout=10)
        assert frame is not None
        return frame.payload
    try:
        yield exchange
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.parametrize("declared_cost", [0, -999999, 999999999999, {"untrusted": True}])
async def test_premium_price_ignores_every_client_money_value(server, declared_cost):
    _, port, _ = server
    async with connected(port) as exchange:
        login = await exchange(LOGIN)
        assert login["user"]["money"] == 100
        assert abs(login["gameTime"] - time.time()) < 10
        assert login["gameTime"] == login["serverTime"]
        request = {"type": "Building", "action": "upgradeImmediatelyByGoldWhenLackRes",
                   "cityId": 1, "buildingId": 5, "inner": 1, "x": 0, "y": 2,
                   "clientMoneyCost": declared_cost, "woodNeed": 0}
        result = await exchange(request)
        assert result["ret"] == 0 and result["goldCost"] == 1
        assert result["building"]["level"] == 2
        assert result["cityResource"]["wood"] == 9000
        assert abs(result["cityResource"]["lastupdate"] - time.time()) < 10
        after = await exchange(LOGIN)
        assert after["user"]["money"] == 99
        assert after["memCityResource"]["gold"] == 10000
        assert next(b["level"] for b in after["sysBuildings"] if b["bid"] == 5) == 2


async def test_draft_query_completion_relogin_and_no_request_dedup(server):
    _, port, clock = server
    async with connected(port) as exchange:
        login = await exchange(LOGIN)
        barracks = next(b for b in login["sysBuildings"] if b["bid"] == 9)
        request = {"type": "Army", "action": "startDraftQueue", "cityId": 1,
                   "sid": 2, "soldierCount": 1, "x": 4, "y": 4, "requestId": "same",
                   "costs": {"food": 0, "wood": 0, "iron": 0}}
        first = await exchange(request)
        second = await exchange(request)
        assert first["ret"] == second["ret"] == 0
        assert len(second["queues"]) == 2
        sync = await exchange({"type": "City", "action": "synchCityResource", "cityId": 1})
        assert sync["cityRes"]["wood"] == 9800
        assert sync["cityRes"]["food"] == 9840
        state = await exchange({"type": "Building", "action": "getBuildingState", "cityId": 1})
        assert state["soldierQueue"][0]["queueCount"] == 2
        clock[0] += 42  # Two sequential trunc(float32(21.739...)) durations.
        completion = await exchange({"type": "User", "action": "checkAQueue", "cityId": 1,
                                     "queueId": second["queues"][1]["qid"], "queueType": 1})
        assert completion["ret"] == 0 and completion["needTry"] is False
        queues = await exchange({"type": "User", "action": "getAllQueue", "cityId": 1})
        assert queues["soldierQueue"] == []
        info = await exchange({"type": "Building", "action": "getBuildingInfo", "cityId": 1, "uniqueId": barracks["id"]})
        assert next(a["count"] for a in info["armys"] if a["sid"] == 2) == 2
    async with connected(port) as exchange:
        login = await exchange(LOGIN)
        assert next(a["count"] for a in login["sysCitySoldiers"] if a["sid"] == 2) == 2


async def test_auth_and_city_ownership_before_any_mutation(server):
    _, port, _ = server
    request = {"type": "Army", "action": "startDraftQueue", "cityId": 1,
               "sid": 2, "soldierCount": 1, "x": 4, "y": 4}
    async with connected(port) as exchange:
        assert (await exchange(request))["ret"] == 900101
        await exchange(LOGIN)
        assert (await exchange({**request, "cityId": 2}))["ret"] == 900102
        assert (await exchange({**request, "type": "Building"}))["ret"] == 900104
        assert (await exchange({**request, "sid": 999}))["ret"] == 900105
        login = await exchange(LOGIN)
        assert login["memCityResource"]["wood"] == 10000


async def test_worker_completes_without_a_client_completion_request(server):
    running, port, clock = server
    async with connected(port) as exchange:
        await exchange(LOGIN)
        await exchange({"type": "Building", "action": "upgrade", "cityId": 1,
                        "buildingId": 5, "inner": 1, "x": 0, "y": 2})
    clock[0] += 60
    # Read raw state, not gameplay.snapshot(), so only the worker can finish it.
    async with asyncio.timeout(10):
        while True:
            async with running.router.engine._connection() as db:
                async with db.execute("SELECT level FROM buildings WHERE bid=5") as cur:
                    if (await cur.fetchone())[0] == 2:
                        break
            await asyncio.sleep(0.05)
    await running.close()
    assert running._queue_worker is None
