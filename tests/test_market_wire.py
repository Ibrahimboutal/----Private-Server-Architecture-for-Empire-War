"""Market research over real loopback frames, including contained simulation."""

import asyncio
from contextlib import asynccontextmanager
import json
import struct

import pytest

from empire_war_private_server.auth.session_manager import SessionManager
from empire_war_private_server.config import ServerConfig
from empire_war_private_server.economy.gameplay_engine import GameplayEngine
from empire_war_private_server.economy.market_engine import MarketEngine
from empire_war_private_server.economy.resource_engine import CityResource, ResourceEngine
from empire_war_private_server.main import GameServer
from empire_war_private_server.protocol.frame_parser import FrameCodec, xor_crypt
from empire_war_private_server.protocol.packet_router import PacketRouter

LOGIN = {"type": "User", "action": "doLogin", "passport": "market-wire", "password": "synthetic-only"}
SELL = {"type": "Market", "action": "sellToUser", "cityId": 1,
        "resType": 0, "resCount": 1, "gold": 0.12, "hour": 12, "unionSel": 0}
OVERFLOW = {**SELL, "gold": 2147483646.0}
SYNC = {"type": "City", "action": "synchCityResource", "cityId": 1}
CODEC = FrameCodec()


@asynccontextmanager
async def serve(tmp_path, *, simulation=False, threshold=2, delay=10):
    resources = ResourceEngine(tmp_path / "state.db", tmp_path / "audit.db")
    await resources.initialize()
    await resources.seed_synthetic_user(LOGIN["passport"], LOGIN["password"], CityResource(
        food=10000, wood=10000, iron=10000, rock=10000, gold=10000, people=1000))
    game = GameplayEngine(resources)
    await game.seed_city(1, 1)
    market = MarketEngine(resources, simulate_int32_crash=simulation,
                          overflow_breaker_threshold=threshold, simulation_delay_ms=delay)
    server = GameServer(ServerConfig(), PacketRouter(resources, SessionManager(), game, market))
    port = await server.start(port=0)
    try:
        yield server, market, port
    finally:
        await server.close()


@asynccontextmanager
async def connection(port):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        yield reader, writer
    finally:
        writer.close()
        await writer.wait_closed()


async def exchange(reader, writer, request, mode=4):
    writer.write(CODEC.encode_frame(mode, request))
    await writer.drain()
    frame = await CODEC.read_frame(reader, timeout=10)
    return None if frame is None else frame.payload


@pytest.mark.parametrize("mode", [4, 8])
async def test_safe_boundaries_preserve_connection_balances_and_frame_audit(tmp_path, mode):
    async with serve(tmp_path) as (server, market, port):
        async with connection(port) as (reader, writer):
            await exchange(reader, writer, LOGIN)
            before = await exchange(reader, writer, SYNC)
            for gold, hour, count, expected in [
                (2147483646.0, 12, 1, 19011), (2147483647.0, 12, 1, 19011),
                (1e308, 2, 1, 19011), (1, 1, 10**400, 19011),
                (2147483647.0, 1, 1, 900105),  # In int32, but no supported sale quote.
            ]:
                response = await exchange(reader, writer, {**SELL, "gold": gold, "hour": hour,
                                                           "resCount": count, "case": "local-boundary"}, mode)
                assert response["ret"] == expected
            after = await exchange(reader, writer, SYNC)
            assert before["cityRes"] == after["cityRes"]
        async with server.router.engine._connection() as db:
            async with db.execute("SELECT * FROM audit.market_events ORDER BY id") as cur:
                events = await cur.fetchall()
        assert len(events) == 5
        for event in events:
            packet, metadata = json.loads(event["packet_json"]), json.loads(event["frame_json"])
            wire = CODEC.encode_frame(mode, packet)
            assert metadata["packet_type"] == mode
            assert metadata["wire_length"] == len(wire) - 5
            assert metadata["plaintext_length"] == CODEC.decode_frame(wire).plaintext_length
        assert (await market.research_status())["simulation_enabled"] is False


async def test_sale_query_and_building_market_count_match_captured_shapes(tmp_path):
    async with serve(tmp_path) as (_, market, port):
        async with connection(port) as (reader, writer):
            await exchange(reader, writer, LOGIN)
            data = await exchange(reader, writer, {**SELL, "action": "getUserSellData"})
            assert set(data) == {"type", "action", "ret", "citySellData"}
            assert data["citySellData"]["foodPriceMax"] == 0.12
            result = await exchange(reader, writer, SELL)
            assert result["ret"] == 0 and result["trade"]["count"] == 1
            assert set(result["trade"]) == set(market._profile["trade_template"])
            assert result["trade"]["price"] == 0.12
            own = await exchange(reader, writer, {**SELL, "action": "doGetCityTrade"})
            assert set(own["tradingList"][0]) == set(market._profile["own_list_template"])
            assert own["tradingList"][0]["id"] == result["trade"]["id"]
            state = await exchange(reader, writer, {"type": "Building", "action": "getBuildingState", "cityId": 1})
            assert state["market"]["myCount"] == 1
            assert (await exchange(reader, writer, SYNC))["cityRes"]["food"] == 9999


async def test_session_and_city_validation_precede_simulation_and_logging(tmp_path):
    async with serve(tmp_path, simulation=True) as (server, market, port):
        async with connection(port) as (reader, writer):
            assert (await exchange(reader, writer, OVERFLOW))["ret"] == 900101
            await exchange(reader, writer, LOGIN)
            assert (await exchange(reader, writer, {**OVERFLOW, "cityId": 2}))["ret"] == 900102
            assert (await exchange(reader, writer, {**OVERFLOW, "type": "Army"}))["ret"] == 900104
            assert (await exchange(reader, writer, SYNC))["ret"] == 0
        assert (await market.research_status())["consecutive_overflows"] == 0
        async with server.router.engine._connection() as db:
            async with db.execute("SELECT COUNT(*) FROM audit.market_events") as cur:
                assert (await cur.fetchone())[0] == 0


async def test_simulation_closes_only_triggering_connections_and_breaker_restores_safe_responses(tmp_path):
    async with serve(tmp_path, simulation=True, threshold=2) as (server, market, port):
        async with connection(port) as (healthy_reader, healthy_writer):
            await exchange(healthy_reader, healthy_writer, LOGIN)
            for expected in (1, 2):
                async with connection(port) as (reader, writer):
                    await exchange(reader, writer, LOGIN)
                    async with asyncio.timeout(10):
                        assert await exchange(reader, writer, OVERFLOW) is None
                assert (await exchange(healthy_reader, healthy_writer, SYNC))["ret"] == 0
                assert (await market.research_status())["consecutive_overflows"] == expected
            assert (await exchange(healthy_reader, healthy_writer, OVERFLOW))["ret"] == 19011
            assert (await exchange(healthy_reader, healthy_writer, SYNC))["ret"] == 0
        assert (await market.research_status())["breaker_tripped"] is True
        assert not server._closing


async def test_simulated_delay_releases_database_and_shutdown_cancels_it(tmp_path):
    async with serve(tmp_path, simulation=True, delay=1000) as (server, market, port):
        async with connection(port) as (reader, writer):
            await exchange(reader, writer, LOGIN)
            writer.write(CODEC.encode_payload(OVERFLOW))
            await writer.drain()
            async with asyncio.timeout(10):
                while (await market.research_status())["consecutive_overflows"] != 1:
                    await asyncio.sleep(0.01)
            # A new write can commit while the failed connection is delayed.
            async with server.router.engine._transaction() as db:
                await db.execute("UPDATE users SET money=money WHERE id=1")
            async with asyncio.timeout(3):
                await server.close()
            assert await reader.read() == b""
            assert not server._clients


@pytest.mark.parametrize("bad_number", ["NaN", "Infinity", "1e9999"])
async def test_nonfinite_wire_json_is_rejected_by_gateway_and_server_recovers(tmp_path, bad_number):
    async with serve(tmp_path) as (_, _, port):
        async with connection(port) as (reader, writer):
            await exchange(reader, writer, LOGIN)
            plaintext = json.dumps(SELL).replace('0.12', bad_number).encode()
            body = xor_crypt(plaintext)
            writer.write(struct.pack(">BI", 4, len(body)) + body)
            await writer.drain()
            async with asyncio.timeout(10):
                assert await reader.read() == b""
        async with connection(port) as (reader, writer):
            assert (await exchange(reader, writer, LOGIN))["ret"] == 0
            assert (await exchange(reader, writer, OVERFLOW))["ret"] == 19011


@pytest.mark.parametrize("changes", [
    {"simulate_int32_crash": "false"}, {"simulate_int32_crash": 1},
    {"overflow_breaker_threshold": 0}, {"overflow_breaker_threshold": True},
    {"simulation_delay_ms": 1001}, {"simulation_delay_ms": -1},
    {"market_scenario": "unknown"}, {"market_scenario": "disabled", "simulate_int32_crash": True},
])
def test_invalid_research_configuration_fails_before_server_start(changes):
    with pytest.raises(ValueError):
        ServerConfig(**changes)
