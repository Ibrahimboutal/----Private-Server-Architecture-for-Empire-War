"""Defense-in-depth Python socket audit guard for the local research process.

This is not an OS firewall or a boundary against hostile Python extensions.
It prevents accidental non-loopback connects/binds through Python socket APIs.
"""

from __future__ import annotations

import sys
from typing import Any


def _check(event: str, args: tuple[Any, ...]) -> None:
    if event in ("socket.connect", "socket.bind"):
        address = args[1]
        if not isinstance(address, tuple) or len(address) != 2 or address[0] != "127.0.0.1":
            raise PermissionError("research server permits only IPv4 loopback networking")
    elif event == "socket.getaddrinfo":
        if args[0] != "127.0.0.1":
            raise PermissionError("external DNS is disabled in the research server")
    elif event == "socket.sendto":
        address = args[-1]
        if not isinstance(address, tuple) or len(address) != 2 or address[0] != "127.0.0.1":
            raise PermissionError("external datagrams are disabled in the research server")
    elif event in ("socket.gethostbyname", "socket.gethostbyaddr"):
        if args[0] != "127.0.0.1":
            raise PermissionError("external DNS is disabled in the research server")


def install_loopback_guard() -> None:
    """Install once at process startup, before constructing the asyncio loop."""
    sys.addaudithook(_check)
