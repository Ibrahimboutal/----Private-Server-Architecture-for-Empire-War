"""Owned LDPlayer routing lifecycle, with no real device/network mutations."""

import json
import asyncio
import base64
import shlex
import subprocess

import pytest

from empire_war_private_server.config import ServerConfig
from tools.local_android_route import AndroidRoute, CHAIN, RouteError
from empire_war_private_server.protocol.frame_parser import FrameCodec, ProtocolError


def completed(code=0, data=b""):
    return subprocess.CompletedProcess([], code, stdout=data, stderr=b"")


class Device(AndroidRoute):
    def __init__(self, path, *, mapping=None):
        super().__init__(ServerConfig(database=path / "state.db"), "emulator-5554")
        self.reverse = mapping
        self.chain = False
        self.jump = None
        self.redirect = None
        self.stops = 0
        self.fail_jump = False
        self.fail_probe = False
        self.calls = []

    async def host_probe(self):
        if self.fail_probe:
            raise RouteError("no local synthetic server")

    def command(self, *args, check=True, data=None):
        self.calls.append(args)
        if args == ("get-state",):
            return completed(data=b"device\n")
        if args == ("shell", "su -c id"):
            return completed(data=b"uid=0(root)\n")
        if args == ("reverse", "--list"):
            return completed(data=(f"device tcp:1024 {self.reverse}\n".encode() if self.reverse else b""))
        if args == ("reverse", "--no-rebind", "tcp:1024", "tcp:9999"):
            assert self.reverse is None
            self.reverse = "tcp:9999"
            return completed()
        if args == ("reverse", "--remove", "tcp:1024"):
            self.reverse = None
            return completed()
        if args == ("shell", "am", "force-stop", "com.feelingtouch.empirewaronline"):
            self.stops += 1
            return completed()
        raise AssertionError(args)

    def iptables(self, *args, check=True):
        self.calls.append(args)
        if args == ("-S", CHAIN):
            if not self.chain:
                return completed(1)
            lines = [shlex.join(["-N", CHAIN])]
            if self.redirect:
                lines.append(shlex.join(["-A", CHAIN, *self.redirect]))
            return completed(data=("\n".join(lines) + "\n").encode())
        if args == ("-N", CHAIN):
            self.chain = True
        elif args[:2] == ("-A", CHAIN):
            self.redirect = list(args[2:])
        elif args[:3] == ("-I", "OUTPUT", "1"):
            if self.fail_jump:
                raise RouteError("jump failure")
            self.jump = list(args[3:])
        elif args[:2] == ("-C", "OUTPUT"):
            return completed(0 if self.jump == list(args[2:]) else 1)
        elif args[:2] == ("-C", CHAIN):
            return completed(0 if self.redirect == list(args[2:]) else 1)
        elif args[:2] == ("-D", "OUTPUT"):
            assert self.jump == list(args[2:])
            self.jump = None
        elif args[:2] == ("-D", CHAIN):
            assert self.redirect == list(args[2:])
            self.redirect = None
        elif args == ("-X", CHAIN):
            assert self.redirect is None and self.jump is None
            self.chain = False
        else:
            raise AssertionError(args)
        return completed()


@pytest.mark.parametrize("mapping", [None, "tcp:9999"])
async def test_start_stop_owns_only_its_rules_and_new_tunnel(tmp_path, mapping):
    device = Device(tmp_path, mapping=mapping)
    await device.start()
    assert device.status()["active"]
    assert device.stops == 1
    assert device.load_state()["reverse_created"] is (mapping is None)
    assert "127.0.0.1:1024" in device.redirect
    assert device.jump[:4] == ["-p", "tcp", "--dport", "1024"]
    before = len(device.calls)
    await device.start()
    assert device.stops == 1  # Already-active start does not interrupt anything.
    assert not any(c[0] in ("-A", "-I", "-N", "-D") for c in device.calls[before:])
    device.stop()
    assert not device.chain and device.jump is None
    assert device.reverse == mapping
    assert not device.state_path.exists()
    assert not any("-F" in c or "--remove-all" in c or "kill-server" in c for c in device.calls)


@pytest.mark.parametrize("mapping", [None, "tcp:9999"])
async def test_partial_install_failure_rolls_back_without_touching_existing_tunnel(tmp_path, mapping):
    device = Device(tmp_path, mapping=mapping)
    device.fail_jump = True
    with pytest.raises(RouteError, match="jump failure"):
        await device.start()
    assert not device.chain and device.jump is None
    assert device.reverse == mapping
    assert not device.state_path.exists()


@pytest.mark.parametrize("conflict", ["chain", "mapping", "probe"])
async def test_preflight_conflict_changes_nothing_and_does_not_stop_game(tmp_path, conflict):
    device = Device(tmp_path)
    device.chain = conflict == "chain"
    device.reverse = "tcp:8888" if conflict == "mapping" else None
    device.fail_probe = conflict == "probe"
    with pytest.raises(RouteError):
        await device.start()
    assert device.stops == 0
    assert not device.state_path.exists()
    assert not any(c[0] in ("-A", "-I", "-N", "-D") for c in device.calls)


async def test_unowned_rule_added_to_chain_prevents_cleanup(tmp_path):
    device = Device(tmp_path)
    await device.start()
    device.redirect += ["unowned"]
    before = len(device.calls)
    with pytest.raises(RouteError, match="unowned"):
        device.stop()
    assert device.chain and device.state_path.exists()
    assert not any(c[0] in ("-D", "-X") for c in device.calls[before:])


async def test_changed_reverse_mapping_is_preserved(tmp_path):
    device = Device(tmp_path)
    await device.start()
    device.reverse = "tcp:8888"
    with pytest.raises(RouteError, match="ownership"):
        device.stop()
    assert device.reverse == "tcp:8888" and device.chain


async def test_restart_after_device_rules_disappear_requires_explicit_cleanup(tmp_path):
    device = Device(tmp_path)
    await device.start()
    device.reverse, device.chain, device.jump, device.redirect = None, False, None, None
    assert device.status()["active"] is False
    with pytest.raises(RouteError, match="incomplete"):
        await device.start()
    device.stop()
    assert not device.state_path.exists()
    await device.start()
    assert device.status()["active"]


def test_no_ownership_record_never_removes_device_rules(tmp_path):
    device = Device(tmp_path, mapping="tcp:9999")
    with pytest.raises(RouteError, match="No owned"):
        device.stop()
    assert device.calls == [] and device.reverse == "tcp:9999"


@pytest.mark.parametrize("change", [{"serial": "other"}, {"tag": "malicious;command"},
                                    {"game_port": 80}, {"reverse_created": "true"}])
async def test_tampered_ownership_record_refuses_mutations(tmp_path, change):
    device = Device(tmp_path)
    await device.start()
    state = device.load_state()
    device.save_state({**state, **change})
    before = len(device.calls)
    with pytest.raises(RouteError):
        device.stop()
    assert len(device.calls) == before


def test_subprocess_pins_adb_server_and_uses_argument_list(tmp_path, monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return completed(data=b"device")
    monkeypatch.setattr(subprocess, "run", run)
    route = AndroidRoute(ServerConfig(database=tmp_path / "state.db"), "emulator-5554")
    route.command("get-state")
    command, kwargs = calls[0]
    assert command == ["adb", "-H", "127.0.0.1", "-P", "5037", "-s", "emulator-5554", "get-state"]
    assert not kwargs.get("shell", False)
    assert kwargs["timeout"] == 20


@pytest.mark.parametrize("valid", [True, False])
async def test_android_probe_keeps_stdin_open_and_decodes_crlf_wrapped_output(tmp_path, monkeypatch, valid):
    device = Device(tmp_path)
    await device.start()
    codec = FrameCodec()
    payload = {"ret": 0, "user": {"name": "Research User"}, "city": {"cid": 1}}
    wire = codec.encode_frame(8, payload) if valid else b"bad frame"
    encoded = base64.b64encode(wire)
    output = b"\r\n".join(encoded[index:index+20] for index in range(0, len(encoded), 20)) + b"\r\n"
    class Input:
        closed = False
        sent = b""
        def write(self, data):
            self.sent += data
        async def drain(self):
            assert not self.closed
        def close(self):
            self.closed = True
    class Process:
        def __init__(self):
            self.stdin = Input()
            self.stdout = asyncio.StreamReader()
            self.stdout.feed_data(output)
            self.stdout.feed_eof()
        async def wait(self):
            assert self.stdin.closed
            return 0
    process = Process()
    async def create(*args, **kwargs):
        assert args[:7] == ("adb", "-H", "127.0.0.1", "-P", "5037", "-s", "emulator-5554")
        assert args[-1] == "nc -w 3 -W 3 127.0.0.2 1024 | base64"
        return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    if valid:
        await device.probe()
    else:
        with pytest.raises(ProtocolError):
            await device.probe()
    assert process.stdin.closed
    assert codec.decode_frame(process.stdin.sent).payload == device.login_packet()


@pytest.mark.parametrize("state", [[], {"version": 1, "serial": "emulator-5554", "proxy_port": 9999,
                                       "game_port": 1024, "tag": 5, "reverse_created": True}])
def test_invalid_ownership_document_is_rejected_cleanly(tmp_path, state):
    device = Device(tmp_path)
    device.state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(RouteError, match="ownership"):
        device.load_state()
    assert device.calls == []
