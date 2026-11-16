"""The opt-in local bridge never authenticates or persists a client account."""

import asyncio
from copy import deepcopy
import json
import sqlite3
from pathlib import Path

import pytest

from empire_war_private_server.auth.session_manager import ConnectionContext, SessionManager
from empire_war_private_server.config import ServerConfig
from empire_war_private_server.economy.resource_engine import CityResource, ResourceEngine
from empire_war_private_server.main import GameServer
from empire_war_private_server.protocol.frame_parser import FrameCodec
from empire_war_private_server.protocol.packet_router import PacketRouter
from empire_war_private_server.economy.gameplay_engine import GameplayEngine
from empire_war_private_server.economy.market_engine import MarketEngine
from empire_war_private_server.economy.client_catalog import load_client_catalog

LOCAL = ("seeded-local", "local-test-password")
CLIENT = {"type": "User", "action": "doLogin", "passport": "dummy-android-account",
          "password": "dummy-android-password", "phoneOs": "android", "locale": "en",
          "serverId": "test", "version": 11520}
SYNC = {"type": "City", "action": "synchCityResource", "cityId": 1001}


@pytest.fixture
async def ledger(tmp_path):
    engine = ResourceEngine(tmp_path / "state.db", tmp_path / "audit.db")
    await engine.initialize()
    await engine.seed_synthetic_user(*LOCAL, CityResource(food=1234))
    return engine


async def test_bridge_is_explicit_and_authenticates_only_seeded_credentials(ledger, monkeypatch):
    strict = PacketRouter(ledger, SessionManager())
    assert (await strict.dispatch(CLIENT, ConnectionContext()))["ret"] == 900101
    assert not ServerConfig().synthetic_client_login
    original = ledger.authenticate
    calls = []

    async def record(passport, password):
        calls.append((passport, password))
        return await original(passport, password)

    monkeypatch.setattr(ledger, "authenticate", record)
    router = PacketRouter(ledger, SessionManager(), synthetic_credentials=LOCAL)
    context = ConnectionContext()
    request = deepcopy(CLIENT)
    response = await router.dispatch(request, context)
    assert response["ret"] == 0
    assert calls == [LOCAL] and request == CLIENT
    assert response["user"]["passport"] == LOCAL[0]
    assert response["city"]["cid"] == response["user"]["lastcid"] == 1001
    assert response["user"]["uid"] == 1
    assert all(value not in json.dumps(response) for value in (CLIENT["passport"], CLIENT["password"]))
    assert (await router.dispatch(SYNC, context))["ret"] == 0
    assert (await router.dispatch({**SYNC, "cityId": 2}, context))["ret"] == 900102


async def test_different_client_accounts_share_only_local_city_without_registration(ledger):
    router = PacketRouter(ledger, SessionManager(), synthetic_credentials=LOCAL)
    responses = []
    for passport in (CLIENT["passport"], "second-dummy-client"):
        responses.append(await router.dispatch({**CLIENT, "passport": passport}, ConnectionContext()))
    assert all(r["ret"] == 0 and r["user"]["uid"] == 1 for r in responses)
    assert responses[0]["gameUUID"] != responses[1]["gameUUID"]
    async with ledger._connection() as db:
        async with db.execute("SELECT passport FROM users") as cursor:
            assert [row["passport"] for row in await cursor.fetchall()] == [LOCAL[0]]
        async with db.execute("SELECT action FROM audit.economy_events") as cursor:
            assert [row["action"] for row in await cursor.fetchall()] == ["synthetic_seed"]


@pytest.mark.parametrize("change", [{"passport": None}, {"password": []},
    {"passport": "x" * 129}, {"password": "x" * 257}])
async def test_malformed_bridge_login_revokes_session(ledger, change):
    router = PacketRouter(ledger, SessionManager(), synthetic_credentials=LOCAL)
    context = ConnectionContext()
    assert (await router.dispatch(CLIENT, context))["ret"] == 0
    old = context.session_id
    assert (await router.dispatch({**CLIENT, **change}, context))["ret"] == 900101
    assert context.session_id is None and router.sessions.get(old) is None
    assert (await router.dispatch(SYNC, context))["ret"] == 900101


@pytest.mark.parametrize("fields", [
    {"passport": ""}, {"password": ""}, {"passport": "", "password": ""},
])
async def test_bridge_accepts_empty_native_strings_but_strict_login_rejects_them(ledger, fields):
    request = {**CLIENT, **fields}
    strict = PacketRouter(ledger, SessionManager())
    assert (await strict.dispatch(request, ConnectionContext()))["ret"] == 900101
    bridge = PacketRouter(ledger, SessionManager(), synthetic_credentials=LOCAL)
    context = ConnectionContext()
    response = await bridge.dispatch(request, context)
    assert response["ret"] == 0 and response["user"]["passport"] == LOCAL[0]
    assert (await bridge.dispatch(SYNC, context))["ret"] == 0


async def test_bridge_fails_closed_if_local_account_credentials_do_not_match(ledger):
    router = PacketRouter(ledger, SessionManager(), synthetic_credentials=(LOCAL[0], "wrong-local"))
    context = ConnectionContext()
    assert (await router.dispatch(CLIENT, context))["ret"] == 900101
    assert context.session_id is None


async def test_bridge_login_and_sync_over_binary_tcp(ledger):
    gameplay = GameplayEngine(ledger)
    await gameplay.seed_city(1, 1)
    router = PacketRouter(ledger, SessionManager(), gameplay, synthetic_credentials=LOCAL)
    server = GameServer(ServerConfig(), router)
    port = await server.start(port=0)
    codec = FrameCodec()
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        startup = [
            {"type": "City", "action": "dummyChangePosition", "userId": 1},
            {"type": "User", "action": "getCityInfo", "cityId": 1001},
            {"type": "User", "action": "setUserPhoneInfo", "country": "local",
             "phoneOs": "android", "phoneModel": "test emulator"},
            {"type": "Mail", "action": "getUnreadCount"},
            {"type": "Event", "action": "getApkUpgradeReward", "deviceType": "android",
             "packageChannel": "android", "version": 11520},
            {"type": "Event", "action": "getLoginShowInfo"},
            {"type": "Event", "action": "getQuestEvent"},
            {"type": "Shop", "action": "getProductKeyList"},
            {"type": "User", "action": "getPayInfo"},
        ]
        for request in startup:
            assert (await router.dispatch(request, ConnectionContext()))["ret"] == 900101
        for request in (CLIENT, *startup, SYNC):
            writer.write(codec.encode_payload(request))
            await writer.drain()
            frame = await codec.read_frame(reader, timeout=10)
            assert frame is not None and frame.payload["ret"] == 0
            if request is CLIENT:
                catalog = load_client_catalog()
                assert all(frame.payload[name] == values for name, values in catalog.items())
                assert frame.packet_type == 8
            elif request["action"] == "dummyChangePosition":
                assert frame.payload == {"type": "City", "action": "dummyChangePosition",
                                         "ret": 0, "targetCid": 1001}
            elif request["action"] == "getCityInfo":
                assert frame.payload["city"]["cid"] == frame.payload["memCityResource"]["cid"] == 1001
                assert all(b["cid"] == 1001 for b in frame.payload["sysBuildings"])
    finally:
        writer.close()
        await writer.wait_closed()
        await server.close()


async def test_position_recovery_preserves_the_city_and_rejects_unowned_ids(ledger):
    router = PacketRouter(ledger, SessionManager(), market=MarketEngine(ledger),
                          synthetic_credentials=LOCAL)
    context = ConnectionContext()
    old = await router.dispatch({**CLIENT, "phoneOs": "research"}, context)
    old_session = router.sessions.get(context.session_id)
    assert old["city"]["cid"] == 1
    place = {"type": "City", "action": "dummyChangePosition", "userId": 1}
    for other in (None, "1", True, 2):
        assert (await router.dispatch({**place, "userId": other}, context))["ret"] == 900102
    for _ in range(2):
        placed = await router.dispatch(place, context)
        assert placed == {"type": "City", "action": "dummyChangePosition", "ret": 0, "targetCid": 1001}
    assert context.session_id == old_session.session_id
    assert router.sessions.get(context.session_id).expires_at == old_session.expires_at
    for other in (None, True, 1, 1002):
        assert (await router.dispatch({**SYNC, "cityId": other}, context))["ret"] == 900102
    assert (await router.dispatch(SYNC, context))["cityRes"]["food"] == 1234
    sale = {"type": "Market", "action": "sellToUser", "cityId": 1001, "resType": 0,
            "resCount": 1, "gold": 0.12, "hour": 12, "unionSel": 0}
    assert (await router.dispatch(sale, context))["trade"]["cid"] == 1001
    assert (await ledger.get_resources(1, 1)).food == 1233
    async with ledger._connection() as db:
        async with db.execute("SELECT city_id, packet_json FROM audit.market_events") as cursor:
            entry = await cursor.fetchone()
            assert entry["city_id"] == 1 and json.loads(entry["packet_json"])["cityId"] == 1001
    # After reconnect, the same native profile sees the same city and balance.
    login = await router.dispatch(CLIENT, context)
    assert login["cityList"][0]["cid"] == login["memCityResource"]["cid"] == 1001
    assert login["memCityResource"]["food"] == 1233


async def test_city_recovery_response_fields_match_both_captured_sequences(ledger):
    path = Path(__file__).resolve().parents[1] / "new-set-up/game_protocol_v2.db"
    if not path.is_file():
        pytest.skip("optional local evidence database unavailable")
    router = PacketRouter(ledger, SessionManager(), synthetic_credentials=LOCAL)
    context = ConnectionContext()
    await router.dispatch(CLIENT, context)
    local = await router.dispatch({"type": "User", "action": "getCityInfo", "cityId": 1001}, context)
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        for start in (23417, 25103):
            rows = db.execute("SELECT payload_json FROM captured_packets WHERE id BETWEEN ? AND ? ORDER BY id",
                              (start, start + 3)).fetchall()
            place, target, info, captured = (json.loads(row[0]) for row in rows)
            assert place["action"] == "dummyChangePosition" and set(place) == {"type", "action", "userId"}
            assert target["ret"] == 0 and target["targetCid"] == info["cityId"] == captured["city"]["cid"]
            assert all(1 <= coord <= 500 for coord in divmod(target["targetCid"], 1000))
            assert set(local) == set(captured)
            assert set(local["city"]) == set(captured["city"])
            assert set(local["cityList"][0]) == set(captured["cityList"][0])


@pytest.mark.parametrize("invalid", [1, "true", None, []])
def test_bridge_flag_requires_boolean(invalid):
    with pytest.raises(ValueError, match="synthetic_client_login"):
        ServerConfig(synthetic_client_login=invalid)


def test_bridge_flag_loads_from_explicit_config(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("synthetic_client_login: true\n", encoding="utf-8")
    assert ServerConfig.load(path).synthetic_client_login is True
