"""Thin wire adapters; quoted prices and state transitions live on the server."""

from __future__ import annotations

from typing import Any

from ..auth.session_manager import Session
from ..economy.gameplay_engine import GameplayEngine
from ..protocol.city_position import owned_city


async def dispatch_gameplay(request: dict[str, Any], session: Session,
                            gameplay: GameplayEngine) -> dict[str, Any] | None:
    kind, action = request.get("type"), request.get("action")
    supported = {
        ("Army", "startDraftQueue"), ("Army", "dissolveSoldier"),
        ("Building", "getBuildingInfo"),
        ("Building", "getBuildingState"), ("Building", "upgrade"),
        ("Building", "upgradeImmediatelyByGoldWhenLackRes"),
        ("User", "getAllQueue"), ("User", "checkAQueue"),
    }
    if not isinstance(kind, str) or not isinstance(action, str) or (kind, action) not in supported:
        return None
    city_id = owned_city(request, session)
    uid = session.user_id
    if action == "startDraftQueue":
        payload = await gameplay.start_draft(uid, city_id, request.get("sid"),
            request.get("soldierCount"), request.get("x"), request.get("y"))
    elif action == "dissolveSoldier":
        payload = await gameplay.dissolve_soldier(uid, city_id, request.get("sid"),
            request.get("soldierCount"), request.get("x"), request.get("y"))
    elif action in ("upgrade", "upgradeImmediatelyByGoldWhenLackRes"):
        # Deliberately never read clientMoneyCost or client-declared resource costs.
        payload = await gameplay.upgrade(uid, city_id, request.get("buildingId"),
            request.get("x"), request.get("y"), request.get("inner"), immediate=action != "upgrade")
    elif action == "getBuildingInfo":
        payload = await gameplay.building_info(uid, city_id, request.get("uniqueId"))
    elif action == "getBuildingState":
        payload = await gameplay.building_state(uid, city_id)
    elif action == "getAllQueue":
        payload = await gameplay.all_queues(uid, city_id)
    else:
        payload = await gameplay.check_queue(uid, city_id, request.get("queueId"), request.get("queueType"))
    return {"type": kind, "action": action, "ret": 0, **payload}
