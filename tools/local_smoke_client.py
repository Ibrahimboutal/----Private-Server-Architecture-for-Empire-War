"""Local version/login/sync check, optionally exercising a fresh synthetic city."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from empire_war_private_server.config import ServerConfig
from empire_war_private_server.network_isolation import install_loopback_guard
from empire_war_private_server.protocol.frame_parser import FrameCodec


async def check(config: ServerConfig, port: int, *, exercise_gameplay: bool = False,
                client_login: bool = False) -> None:
    codec = FrameCodec()
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        async def exchange(packet: dict) -> dict:
            writer.write(codec.encode_payload(packet))
            await writer.drain()
            async with asyncio.timeout(10):
                frame = await codec.read_frame(reader)
            if frame is None or frame.payload.get("ret") != 0:
                raise RuntimeError("local server did not return success")
            return frame.payload

        version = await exchange({"type": "User", "action": "getServerVersion",
                                  "marketId": 0, "packageChannel": "FT", "phoneOs": "android"})
        assert version["version"] == 1719
        assert version["marketApkUrl"] == "market://details?id=com.feelingtouch.empirewaronline"
        print("PASS: captured Android version handshake succeeds before login.")
        login = await exchange({"type": "User", "action": "doLogin",
            "passport": "" if client_login else config.synthetic_passport,
            "password": "" if client_login else config.synthetic_password,
            "locale": "en_US", "phoneOs": "android" if client_login else "research",
            "serverId": "local", "version": 0})
        assert login["user"]["passport"] == config.synthetic_passport
        if client_login:
            print("PASS: client login maps to the synthetic account without using its supplied credentials.")
            assert login["gameVersion"] == 1719
            assert len(login["cfgSoldier"]) == 99 and len(login["cfgBuilding"]) == 20
            city_id = login["city"]["cid"]
            assert all(1 <= coord <= 500 for coord in divmod(city_id, 1000))
            placement = await exchange({"type": "City", "action": "dummyChangePosition",
                                        "userId": login["user"]["uid"]})
            assert placement["targetCid"] == city_id
            info = await exchange({"type": "User", "action": "getCityInfo", "cityId": city_id})
            assert info["city"]["cid"] == info["memCityResource"]["cid"] == city_id
            print("PASS: city position resolves and the client can load its owned city.")
            for startup in (
                {"type": "User", "action": "setUserPhoneInfo", "country": "local",
                 "phoneOs": "android", "phoneModel": "synthetic emulator"},
                {"type": "Mail", "action": "getUnreadCount"},
                {"type": "Event", "action": "getApkUpgradeReward", "deviceType": "android",
                 "packageChannel": "android", "version": 11520},
                {"type": "Event", "action": "getLoginShowInfo"},
                {"type": "Event", "action": "getQuestEvent"},
                {"type": "Shop", "action": "getProductKeyList"},
                {"type": "User", "action": "getPayInfo"},
            ):
                await exchange(startup)
            print("PASS: complete static catalogs and seven synthetic startup responses are available.")
        cid = login["city"]["cid"]
        sync = await exchange({"type": "City", "action": "synchCityResource", "cityId": cid})
        assert login["memCityResource"] == sync["cityRes"]
        print("PASS: synthetic login and resource sync agree over loopback TCP.")
        if exercise_gameplay:
            quarry = config.gameplay_scenario == "captured-quarry-shortage-v1"
            bid = 3 if quarry else 5
            building = next((b for b in login.get("sysBuildings", []) if b["bid"] == bid), None)
            if building is None or building["level"] != 1 or building["state"] != 0:
                raise RuntimeError("gameplay smoke requires a fresh supported scenario city")
            if not quarry:
                drafted = await exchange({"type": "Army", "action": "startDraftQueue", "cityId": cid,
                                          "sid": 2, "soldierCount": 1, "x": 4, "y": 4})
                assert drafted["queues"][0]["originalTimeLeft"] == 21
            upgraded = await exchange({"type": "Building", "cityId": cid, "buildingId": bid,
                "action": "upgradeImmediatelyByGoldWhenLackRes" if quarry else "upgrade",
                "inner": 0 if quarry else 1, "x": 5 if quarry else 0,
                "y": 1 if quarry else 2, "clientMoneyCost": 0})
            refreshed = await exchange({"type": "City", "action": "synchCityResource", "cityId": cid})
            if quarry:
                assert upgraded["goldCost"] == 6 and upgraded["building"]["level"] == 2
                assert refreshed["cityRes"]["wood"] == 0
                assert refreshed["cityRes"]["rockMax"] == sync["cityRes"]["rockMax"] + 20000
                relogin = await exchange({"type": "User", "action": "doLogin",
                    "passport": config.synthetic_passport, "password": config.synthetic_password})
                assert relogin["user"]["money"] == login["user"]["money"] - 6
            else:
                assert upgraded["building"]["state"] == 1
                assert refreshed["cityRes"]["wood"] == sync["cityRes"]["wood"] - 1100
                assert refreshed["cityRes"]["food"] == sync["cityRes"]["food"] - 280
            print("PASS: capture-derived gameplay charges and state agree over loopback TCP.")
    finally:
        writer.close()
        await writer.wait_closed()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--port", type=int, help="1024 direct, or 9999 through a local-only proxy")
    parser.add_argument("--exercise-gameplay", action="store_true",
                        help="spend synthetic resources on a fresh Phase 2 city")
    parser.add_argument("--client-login", action="store_true",
                        help="test the opt-in login bridge using empty native account strings")
    args = parser.parse_args()
    config = ServerConfig.load(args.config)
    port = args.port if args.port is not None else config.port
    if not 1 <= port <= 65535:
        parser.error("port must be in 1..65535")
    install_loopback_guard()
    asyncio.run(check(config, port, exercise_gameplay=args.exercise_gameplay,
                      client_login=args.client_login))


if __name__ == "__main__":
    main()
