"""Public startup handshake preceding login in the supplied Android captures.

game_protocol_v2.db rows 1/2/3 establish request, response, then doLogin;
all 28 captured responses agree. The native response decoder reads version
and marketApkUrl. This fixed Android/FT profile neither authenticates a user
nor fetches an update. Other platform/channel combinations need evidence.
"""

from __future__ import annotations

from typing import Any

from ..economy.resource_engine import EconomyError


def get_server_version(request: dict[str, Any]) -> dict[str, Any]:
    """Return the captured startup response without touching session state."""
    market_id = request.get("marketId")
    channel, platform = request.get("packageChannel"), request.get("phoneOs")
    if (type(market_id) is not int or not -(2**31) <= market_id < 2**31
            or not isinstance(channel, str) or not 1 <= len(channel) <= 64
            or not isinstance(platform, str) or not 1 <= len(platform) <= 32):
        raise EconomyError(900103, "Invalid version-handshake fields")
    if (market_id, channel, platform) != (0, "FT", "android"):
        raise EconomyError(900105, "No captured version profile for this platform/channel")
    return {"type": "User", "action": "getServerVersion", "ret": 0,
            "version": 1719,
            "marketApkUrl": "market://details?id=com.feelingtouch.empirewaronline"}
