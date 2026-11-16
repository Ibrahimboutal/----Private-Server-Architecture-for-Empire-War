"""Observed City/synchCityResource returns cityRes, not memCityResource."""

from __future__ import annotations

import time
from typing import Any

from ..auth.session_manager import Session
from ..economy.resource_engine import EconomyError, ResourceEngine


async def synch_city_resource(request: dict[str, Any], session: Session,
                              engine: ResourceEngine) -> dict[str, Any]:
    city_id = request.get("cityId")
    if type(city_id) is not int or city_id != session.city_id:
        raise EconomyError(900102, "City is not owned by this session")
    resources = await engine.get_resources(session.user_id, city_id)
    now_ms = time.time_ns() // 1_000_000
    return {"type": "City", "action": "synchCityResource", "ret": 0,
            "cityRes": resources.to_wire(), "gameTime": now_ms, "serverTime": now_ms}
