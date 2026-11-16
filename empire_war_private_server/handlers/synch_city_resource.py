"""Observed City/synchCityResource returns cityRes, not memCityResource."""

from __future__ import annotations

import time
from typing import Any

from ..auth.session_manager import Session
from ..economy.resource_engine import ResourceEngine
from ..protocol.city_position import owned_city
from ..economy.gameplay_engine import GameplayEngine


async def synch_city_resource(request: dict[str, Any], session: Session,
                              engine: ResourceEngine, gameplay: GameplayEngine | None = None) -> dict[str, Any]:
    city_id = owned_city(request, session)
    if gameplay is not None:
        resource_wire = (await gameplay.snapshot(session.user_id, city_id))["resources"]
    else:
        resource_wire = (await engine.get_resources(session.user_id, city_id)).to_wire()
    now_s = time.time_ns() // 1_000_000_000
    return {"type": "City", "action": "synchCityResource", "ret": 0,
            "cityRes": resource_wire, "gameTime": now_s, "serverTime": now_s}
