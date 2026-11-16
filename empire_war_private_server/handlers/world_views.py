"""Captured world views for the synthetic research city and its local terrain.

The native map uses decimal digit packing for world tile IDs. GroundTile's
getTileId at export lines 1908521–1908523 decodes those digits into a city ID.
WorldGround.getBlockId at line 1948523 groups coordinates into 10 by 10 blocks.
The fixed terrain fixture supplies public tile fields and synthetic ownership.
"""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from importlib.resources import files
import json
from typing import Any

from ..auth.session_manager import Session
from ..economy.resource_engine import EconomyError
from ..protocol.city_position import owned_city


SUPPORTED_WORLD_ACTIONS = frozenset({
    ("World", "getMapRawBlockData"), ("World", "getMapGridCityInfo"),
    ("World", "getMyFieldState"), ("World", "getTroopsInWorld"),
    ("Union", "getMyUnionFriendRelation"),
})


@lru_cache(maxsize=1)
def _fixture() -> dict[str, Any]:
    value = json.loads(files("empire_war_private_server").joinpath("data/world_fixture.json").read_text(encoding="utf-8"))
    if value["version"] != 1 or len(value["terrain"]) != 100:
        raise ValueError("unsupported world fixture")
    return value


def _map_city_id(session: Session) -> int:
    city_id = session.wire_city_id
    y, x = divmod(city_id, 1000)
    if not 1 <= x <= 500 or not 1 <= y <= 500:
        raise EconomyError(900105, "City needs a map position before loading the world")
    return city_id


def _coordinate(request: dict[str, Any], key: str) -> int:
    value = request.get(key)
    if type(value) is not int or not 1 <= value <= 500:
        raise EconomyError(900103, f"Invalid {key}")
    return value


def _raw_blocks(request: dict[str, Any], city_id: int) -> dict[str, Any]:
    identifiers = request.get("blockData")
    if type(identifiers) is not list or not 1 <= len(identifiers) <= 9:
        raise EconomyError(900103, "Expected one through nine map block identifiers")
    if any(type(value) is not int or not 0 <= value // 100 <= 50
           or not 0 <= value % 100 <= 50 for value in identifiers):
        raise EconomyError(900103, "Invalid map block identifier")
    if len(set(identifiers)) != len(identifiers):
        raise EconomyError(900103, "Duplicate map block identifier")
    y, x = divmod(city_id, 1000)
    city_block = (y // 10) * 100 + x // 10
    city_offset = (y % 10) * 10 + x % 10
    fixture = _fixture()
    blocks = []
    for identifier in identifiers:
        for offset, terrain in enumerate(fixture["terrain"]):
            tile = dict(terrain, wid=identifier * 100 + offset)
            if identifier == city_block and offset == city_offset:
                tile.update(fixture["city_tile"], ownercid=city_id)
            blocks.append(tile)
    return {"blocks": blocks, "monsters": []}


def _city_information(request: dict[str, Any], session: Session, city_id: int) -> dict[str, Any]:
    cids = request.get("cids")
    if type(cids) is not str or not cids or len(cids) > 700:
        raise EconomyError(900103, "Invalid city identifiers")
    fields = cids.split(",")
    if len(fields) > 100 or any(not field.isascii() or not field.isdecimal() for field in fields):
        raise EconomyError(900103, "Invalid city identifiers")
    requested = [int(field) for field in fields]
    if any(not 1 <= value % 1000 <= 500 or not 1 <= value // 1000 <= 500 for value in requested):
        raise EconomyError(900103, "Invalid city map position")
    if city_id not in requested:
        return {"blocks": []}
    city = dict(_fixture()["city_info_shape"])
    city.update(cid=city_id, uid=session.user_id, cityLevel=1,
                cityName="Research City", userName="Research User")
    return {"blocks": [city]}


def dispatch_world_views(request: dict[str, Any], session: Session) -> dict[str, Any] | None:
    """Serve bounded informational queries after the router authenticates.

    Terrain repeats the reviewed neutral block as a local test landscape. The
    city-info reply describes the same owned city shown at login. Deployed
    troops, occupied fields and diplomatic relationships are empty in this
    scenario. No march, occupation or combat operation is implemented here.
    """
    kind, action = request.get("type"), request.get("action")
    if not isinstance(kind, str) or not isinstance(action, str) or (kind, action) not in SUPPORTED_WORLD_ACTIONS:
        return None
    if "cityId" in request or action == "getMyFieldState":
        owned_city(request, session)
    if action == "getMapRawBlockData":
        payload = _raw_blocks(request, _map_city_id(session))
    elif action == "getMapGridCityInfo":
        payload = _city_information(request, session, _map_city_id(session))
    else:
        if action == "getTroopsInWorld":
            _coordinate(request, "centerX")
            _coordinate(request, "centerY")
            radius = request.get("radius")
            if type(radius) is not int or not 0 <= radius <= 500:
                raise EconomyError(900103, "Invalid radius")
            if radius != 5:
                raise EconomyError(900105, "Only the captured radius-five view is available")
        return deepcopy(_fixture()["empty_replies"][action])
    return {"type": kind, "action": action, "ret": 0, **payload}
