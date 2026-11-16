"""Small synthetic market check; accepts only a localhost port and local config.

Three finite boundary cases exercise the defensive research guard. Optional
listing validation spends one synthetic food. No captures or official endpoint
configuration are loaded. Bounded disconnects are accepted only when explicitly
enabled in the supplied emulator configuration.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from empire_war_private_server.config import ServerConfig
from empire_war_private_server.network_isolation import install_loopback_guard
from empire_war_private_server.protocol.frame_parser import FrameCodec


async def check(config: ServerConfig, port: int, *, exercise_listing: bool = False) -> None:
    """Run bounded cases on fresh authenticated connections, then verify health."""
    codec = FrameCodec()
    login = {"type": "User", "action": "doLogin", "passport": config.synthetic_passport,
             "password": config.synthetic_password}
    sell = {"type": "Market", "action": "sellToUser", "cityId": 1,
            "resType": 0, "resCount": 1, "gold": 0.12, "hour": 12, "unionSel": 0}
    baseline: dict[str, Any] | None = None
    safe, disconnected = 0, 0

    async def exchange(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                       packet: dict[str, Any]) -> dict[str, Any] | None:
        async with asyncio.timeout(10):
            writer.write(codec.encode_payload(packet))
            await writer.drain()
            response = await codec.read_frame(reader)
        return None if response is None else response.payload

    for gold in (2147483646.0, 2147483647.0, 1e308):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            identity = await exchange(reader, writer, login)
            if identity is None or identity.get("ret") != 0:
                raise RuntimeError("synthetic login failed")
            if baseline is None:
                baseline = identity["memCityResource"]
            if identity["memCityResource"] != baseline:
                raise RuntimeError("overflow attempt changed city resources")
            result = await exchange(reader, writer, {**sell, "gold": gold})
            if result is None and config.simulate_int32_crash:
                disconnected += 1
            elif result is not None and result.get("ret") == 19011:
                safe += 1
            else:
                raise RuntimeError("unexpected market boundary response")
        finally:
            writer.close()
            await writer.wait_closed()

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        identity = await exchange(reader, writer, login)
        if identity is None or identity.get("ret") != 0 or identity["memCityResource"] != baseline:
            raise RuntimeError("server health/resource verification failed")
        if config.simulate_int32_crash and config.overflow_breaker_threshold <= 3:
            result = await exchange(reader, writer, {**sell, "gold": 2147483646.0})
            if result is None or result.get("ret") != 19011:
                raise RuntimeError("circuit breaker did not restore safe responses")
        if exercise_listing:
            result = await exchange(reader, writer, sell)
            if result is None or result.get("ret") != 0:
                raise RuntimeError("captured listing was rejected; requires one synthetic food and listing capacity")
            own = await exchange(reader, writer, {"type": "Market", "action": "doGetCityTrade", "cityId": 1})
            if own is None or own.get("ret") != 0 or not any(t["id"] == result["trade"]["id"] for t in own["tradingList"]):
                raise RuntimeError("listing not returned by owned listing query")
            sync = await exchange(reader, writer, {"type": "City", "action": "synchCityResource", "cityId": 1})
            if sync is None or sync.get("ret") != 0 or sync["cityRes"]["food"] != baseline["food"] - 1:
                raise RuntimeError("listing resource deduction mismatch")
            print("PASS: captured listing persisted and deducted one synthetic food.")
    finally:
        writer.close()
        await writer.wait_closed()
    print(f"PASS: market boundaries ({safe} safe responses, {disconnected} contained disconnects); server remains healthy.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--port", type=int, help="1024 directly, or 9999 via the local proxy")
    parser.add_argument("--exercise-listing", action="store_true", help="spend one synthetic food on the captured listing")
    args = parser.parse_args()
    config = ServerConfig.load(args.config)
    if config.market_scenario == "disabled":
        parser.error("the selected configuration disables the market")
    port = config.port if args.port is None else args.port
    if not 1 <= port <= 65535:
        parser.error("port must be in 1..65535")
    install_loopback_guard()
    asyncio.run(check(config, port, exercise_listing=args.exercise_listing))


if __name__ == "__main__":
    main()
