"""Synthetic localhost-only login/sync check; never reads captured credentials."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from empire_war_private_server.config import ServerConfig
from empire_war_private_server.network_isolation import install_loopback_guard
from empire_war_private_server.protocol.frame_parser import FrameCodec


async def check(config: ServerConfig, port: int) -> None:
    codec = FrameCodec()
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        async def exchange(packet: dict) -> dict:
            writer.write(codec.encode_payload(packet))
            await writer.drain()
            async with asyncio.timeout(5):
                frame = await codec.read_frame(reader)
            if frame is None or frame.payload.get("ret") != 0:
                raise RuntimeError("local server did not return success")
            return frame.payload

        login = await exchange({"type": "User", "action": "doLogin",
            "passport": config.synthetic_passport, "password": config.synthetic_password,
            "locale": "en_US", "phoneOs": "research", "serverId": "local", "version": 0})
        cid = login["city"]["cid"]
        sync = await exchange({"type": "City", "action": "synchCityResource", "cityId": cid})
        assert login["memCityResource"] == sync["cityRes"]
        print("PASS: synthetic login and resource sync agree over loopback TCP.")
    finally:
        writer.close()
        await writer.wait_closed()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--port", type=int, help="1024 direct, or 9999 through a local-only proxy")
    args = parser.parse_args()
    config = ServerConfig.load(args.config)
    port = args.port if args.port is not None else config.port
    if not 1 <= port <= 65535:
        parser.error("port must be in 1..65535")
    install_loopback_guard()
    asyncio.run(check(config, port))


if __name__ == "__main__":
    main()
