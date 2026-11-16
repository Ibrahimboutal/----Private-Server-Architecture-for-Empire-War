"""Synthetic login using the observed User/doLogin response field names."""

from __future__ import annotations

from importlib.resources import files
import json
import time
from typing import Any

from ..auth.session_manager import ConnectionContext, SessionManager
from ..economy.resource_engine import EconomyError, ResourceEngine
from ..economy.gameplay_engine import GameplayEngine


async def do_login(request: dict[str, Any], context: ConnectionContext,
                   engine: ResourceEngine, sessions: SessionManager,
                   gameplay: GameplayEngine | None = None) -> dict[str, Any]:
    """Accept only seeded local credentials, returning no captured user values."""
    sessions.revoke(context.session_id)
    context.session_id = None
    passport, password = request.get("passport"), request.get("password")
    if not isinstance(passport, str) or not isinstance(password, str):
        raise EconomyError(900101, "Synthetic credentials required")
    identity = await engine.authenticate(passport, password)
    if identity is None:
        raise EconomyError(900101, "Invalid synthetic credentials")
    user_id, city_id = identity
    resource = await engine.get_resources(user_id, city_id)
    session = sessions.create(user_id, city_id)
    context.session_id = session.session_id
    # Every scalar in this shape was emptied by the offline fixture extractor.
    # These replacements are explicitly synthetic research fixture values.
    response = json.loads(files("empire_war_private_server").joinpath("data/login_shape.json").read_text(encoding="utf-8"))
    now_s = time.time_ns() // 1_000_000_000
    response.update(type="User", action="doLogin", ret=0, memCityResource=resource.to_wire(),
                    gameUUID=session.session_id, serverTime=now_s, gameTime=now_s)
    response["user"].update(uid=user_id, lastcid=city_id, name="Research User",
                            passport=passport, ip="127.0.0.1", locale="en_US")
    response["city"].update(cid=city_id, uid=user_id, name="Research City")
    response["cityList"] = [dict(response["city"], taketime=0)]
    # Only explicit cost fields are available to the current emulator. Empty
    # cfg/building/hero tables deliberately don't pretend full client readiness.
    response["cfgSoldier"] = [dict(sid=sid, **{f"{name}Need": amount for name, amount in costs.items()})
                              for sid, costs in sorted(engine.troop_costs.items())]
    if gameplay is not None:
        snapshot = await gameplay.snapshot(user_id, city_id)
        response.update(gameplay.login_configuration())
        response.update(memCityResource=snapshot["resources"], sysBuildings=snapshot["buildings"],
                        sysCitySoldiers=snapshot["troops"], defaultBuildingQueue=1, maxBuildingQueue=1)
        response["user"]["money"] = snapshot["money"]
    return response
