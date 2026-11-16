"""Manage a reversible LDPlayer TCP route into the localhost test server.

Android's Wi-Fi proxy setting is HTTP-only and does not redirect this socket.
This helper owns one named NAT chain/jump, reuses a matching ADB reverse tunnel,
and never launches the game, changes HTTP bootstrap, flushes NAT, or binds LAN.
LDPlayer lacks the iptables owner match: the rule covers all IPv4 TCP traffic
to the selected game port in this emulator, not just the Empire War UID.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from empire_war_private_server.config import ServerConfig
from empire_war_private_server.network_isolation import install_loopback_guard
from empire_war_private_server.protocol.frame_parser import FrameCodec, ProtocolError

CHAIN = "EW_LOCAL_TCP"
PACKAGE = "com.feelingtouch.empirewaronline"
TAG_PREFIX = "empire-local:"


class RouteError(RuntimeError):
    """An unavailable local prerequisite or an ownership conflict."""


class AndroidRoute:
    """Record ownership before mutations; refuse to overwrite unrelated rules."""

    def __init__(self, config: ServerConfig, serial: str, adb: str = "adb", *,
                 proxy_port: int = 9999, game_port: int = 1024) -> None:
        if not re.fullmatch(r"[A-Za-z0-9._:-]+", serial):
            raise ValueError("invalid ADB serial")
        if any(type(port) is not int or not 1 <= port <= 65535 for port in (proxy_port, game_port)):
            raise ValueError("invalid port")
        self.config, self.serial, self.adb = config, serial, adb
        self.proxy_port, self.game_port = proxy_port, game_port
        # Colon is valid in a TCP ADB serial but not in a Windows filename.
        self.state_path = config.database.parent / ("android-route-" + serial.replace(":", "_") + ".json")

    def command(self, *args: str, check: bool = True, data: bytes | None = None) -> subprocess.CompletedProcess:
        """Pin the shared ADB service to host loopback; never restart it."""
        try:
            result = subprocess.run([self.adb, "-H", "127.0.0.1", "-P", "5037", "-s", self.serial, *args],
                input=data, capture_output=True, timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RouteError(f"Local ADB command failed ({type(exc).__name__})") from exc
        if check and result.returncode:
            # Never include command stdin or arbitrary emulator output in errors.
            raise RouteError(f"ADB {args[0]} failed (exit {result.returncode})")
        return result

    def iptables(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        command = shlex.join(["iptables", "-t", "nat", *args])
        return self.command("shell", shlex.join(["su", "-c", command]), check=check)

    def mapping(self) -> str | None:
        for line in self.command("reverse", "--list").stdout.decode().splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[-2] == f"tcp:{self.game_port}":
                return fields[-1]
        return None

    def load_state(self) -> dict[str, Any] | None:
        if not self.state_path.exists():
            return None
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        if (not isinstance(state, dict) or state.get("version") != 1 or state.get("serial") != self.serial
                or state.get("proxy_port") != self.proxy_port or state.get("game_port") != self.game_port
                or not isinstance(state.get("tag"), str)
                or not re.fullmatch(r"empire-local:[0-9a-f]{32}", state["tag"])
                or type(state.get("reverse_created")) is not bool):
            raise RouteError("Route ownership record does not match this command; refusing changes")
        return state

    def save_state(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.state_path)

    def rules(self, tag: str) -> tuple[list[str], list[str]]:
        match = ["-p", "tcp", "--dport", str(self.game_port), "-m", "comment", "--comment", tag]
        return [*match, "-j", CHAIN], [*match, "-j", "DNAT", "--to-destination", f"127.0.0.1:{self.game_port}"]

    def chain_exists(self) -> bool:
        return self.iptables("-S", CHAIN, check=False).returncode == 0

    def verify_owned_chain(self, state: dict[str, Any]) -> None:
        """Do not clear a named chain with rules belonging to another process."""
        result = self.iptables("-S", CHAIN, check=False)
        if result.returncode:
            return
        _, redirect = self.rules(state["tag"])
        expected = ["-A", CHAIN, *redirect]
        for line in result.stdout.decode().splitlines():
            tokens = shlex.split(line)
            if tokens == ["-N", CHAIN]:
                continue
            # iptables prints the implicit tcp matcher explicitly.
            for index in range(len(tokens) - 1):
                if tokens[index:index + 2] == ["-m", "tcp"]:
                    tokens[index:index + 2] = []
                    break
            if tokens != expected:
                raise RouteError("Local chain contains an unowned rule; left unchanged")

    def status(self) -> dict[str, Any]:
        state = self.load_state()
        mapping = self.mapping()
        active = False
        if state is not None:
            self.verify_owned_chain(state)
            jump, redirect = self.rules(state["tag"])
            active = (mapping == f"tcp:{self.proxy_port}"
                      and self.iptables("-C", "OUTPUT", *jump, check=False).returncode == 0
                      and self.iptables("-C", CHAIN, *redirect, check=False).returncode == 0)
        return {"serial": self.serial, "device_port": self.game_port, "host_proxy_port": self.proxy_port,
                "reverse_target": mapping, "owned_record": state is not None, "active": active,
                "scope": "all emulator IPv4 TCP connections to the game port"}

    def stop(self) -> None:
        state = self.load_state()
        if state is None:
            raise RouteError("No owned route record; no rules or mappings were removed")
        self.verify_owned_chain(state)
        mapping = self.mapping()
        if mapping not in (None, f"tcp:{self.proxy_port}"):
            raise RouteError("Reverse mapping changed ownership; left unchanged")
        # Removing a route must not send a currently running game back online.
        self.command("shell", "am", "force-stop", PACKAGE)
        jump, redirect = self.rules(state["tag"])
        if self.iptables("-C", "OUTPUT", *jump, check=False).returncode == 0:
            self.iptables("-D", "OUTPUT", *jump)
        if self.chain_exists():
            if self.iptables("-C", CHAIN, *redirect, check=False).returncode == 0:
                self.iptables("-D", CHAIN, *redirect)
            self.iptables("-X", CHAIN)
        if state["reverse_created"] and mapping is not None:
            self.command("reverse", "--remove", f"tcp:{self.game_port}")
        self.state_path.unlink()

    async def start(self) -> None:
        """Preflight the actual local service, stop the game, then install rules."""
        if self.load_state() is not None:
            if self.status()["active"]:
                return
            raise RouteError("Recorded route is incomplete; run stop before starting it again")
        if self.command("get-state").stdout.strip() != b"device":
            raise RouteError("Selected Android device is not ready")
        if b"uid=0" not in self.command("shell", "su -c id").stdout:
            raise RouteError("Root is required for local TCP routing")
        if self.chain_exists():
            raise RouteError("An unowned local route chain already exists; left unchanged")
        existing = self.mapping()
        if existing not in (None, f"tcp:{self.proxy_port}"):
            raise RouteError("Another reverse mapping occupies the device game port")
        await self.host_probe()
        self.command("shell", "am", "force-stop", PACKAGE)
        state = {"version": 1, "serial": self.serial, "proxy_port": self.proxy_port,
                 "game_port": self.game_port, "tag": TAG_PREFIX + uuid4().hex, "reverse_created": False}
        self.save_state(state)
        try:
            if existing is None:
                self.command("reverse", "--no-rebind", f"tcp:{self.game_port}", f"tcp:{self.proxy_port}")
                state["reverse_created"] = True
                self.save_state(state)
            self.iptables("-N", CHAIN)
            jump, redirect = self.rules(state["tag"])
            self.iptables("-A", CHAIN, *redirect)
            self.iptables("-I", "OUTPUT", "1", *jump)
            if not self.status()["active"]:
                raise RouteError("Installed route verification failed")
        except BaseException:
            # Ownership record remains if cleanup fails, allowing explicit retry.
            self.stop()
            raise

    def login_packet(self) -> dict[str, Any]:
        return {"type": "User", "action": "doLogin", "passport": self.config.synthetic_passport,
                "password": self.config.synthetic_password}

    @staticmethod
    def require_local_login(payload: dict[str, Any]) -> None:
        if (payload.get("ret") != 0 or payload.get("user", {}).get("name") != "Research User"
                or payload.get("city", {}).get("cid") != 1):
            raise RouteError("The proxy did not return this emulator's synthetic login")

    async def host_probe(self) -> None:
        codec = FrameCodec()
        async with asyncio.timeout(10):
            reader, writer = await asyncio.open_connection("127.0.0.1", self.proxy_port)
            try:
                writer.write(codec.encode_payload(self.login_packet()))
                await writer.drain()
                result = await codec.read_frame(reader)
                if result is None:
                    raise RouteError("Local proxy closed before login response")
                self.require_local_login(result.payload)
            finally:
                writer.close()
                await writer.wait_closed()

    async def probe(self) -> None:
        """Send a synthetic login from Android, proving both NAT and tunnel.

        127.0.0.2 is intentionally a different loopback destination. Without the
        DNAT rule it cannot reach the 127.0.0.1 reverse listener or the internet.
        Android's nc only transports the existing binary protocol bytes.
        """
        if not self.status()["active"]:
            raise RouteError("Local route is not active")
        codec = FrameCodec()
        remote = shlex.join(["nc", "-w", "3", "-W", "3", "127.0.0.2", str(self.game_port)]) + " | base64"
        process = await asyncio.create_subprocess_exec(
            self.adb, "-H", "127.0.0.1", "-P", "5037", "-s", self.serial,
            # This LDPlayer shell expands LF to CRLF even with -T; exec-out is
            # output-only on this device. Base64 protects stdout while shell
            # stdin carries the request. nc's idle deadline ends the encoder.
            "shell", "-T", remote,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            # Keep stdin open until the reply: closing it immediately causes
            # Android nc/ADB to tear down the socket before the frame is relayed.
            async with asyncio.timeout(10):
                process.stdin.write(codec.encode_payload(self.login_packet()))
                await process.stdin.drain()
                encoded = bytearray()
                while block := await process.stdout.read(65536):
                    encoded.extend(block)
                    if len(encoded) > 2 * codec.max_frame_bytes:
                        raise RouteError("Android probe output exceeds its limit")
                if not encoded:
                    raise RouteError("Android probe received no complete response")
                wire = base64.b64decode(b"".join(encoded.split()), validate=True)
                self.require_local_login(codec.decode_frame(wire).payload)
        finally:
            process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except TimeoutError:
                process.kill()
                await process.wait()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "status", "probe", "stop"))
    parser.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    parser.add_argument("--serial", default="emulator-5554")
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--proxy-port", type=int, default=9999)
    parser.add_argument("--game-port", type=int, default=1024)
    args = parser.parse_args()
    try:
        route = AndroidRoute(ServerConfig.load(args.config), args.serial, args.adb,
                             proxy_port=args.proxy_port, game_port=args.game_port)
        install_loopback_guard()
        if args.action == "start":
            asyncio.run(route.start())
            print("Local TCP route installed; game stopped. HTTP bootstrap remains separate; client login uses the server's configured policy.")
        elif args.action == "stop":
            route.stop()
            print("Owned local TCP route removed; game remains stopped. Pre-existing reverse mappings were preserved.")
        elif args.action == "probe":
            asyncio.run(route.probe())
            print("PASS: Android loopback -> owned NAT rule -> ADB reverse -> local proxy -> synthetic login.")
        else:
            print(json.dumps(route.status(), indent=2))
    except (RouteError, ProtocolError, ValueError, OSError, TimeoutError) as exc:
        parser.exit(1, f"Local route failed ({type(exc).__name__}): {exc}\n")


if __name__ == "__main__":
    main()
