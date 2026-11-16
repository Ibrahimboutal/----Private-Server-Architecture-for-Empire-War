"""Small profile panels backed by the local account and captured wire schemas."""

from __future__ import annotations

from importlib.resources import files
import json
from typing import Any

from ..auth.session_manager import Session
from ..economy.gameplay_engine import GameplayEngine, integer
from ..economy.resource_engine import EconomyError, ResourceEngine
from ..protocol.city_position import owned_city

ROUTES = frozenset({("Friend", "getUserRelation"), ("User", "getUserRankValue"),
    ("User", "getUserPh"), ("User", "getUserDynamicFace"),
    ("Target", "getTargetList"), ("Event", "getKingdomRoadInfo")})


async def dispatch_profile_views(request: dict[str, Any], session: Session,
                                 engine: ResourceEngine, gameplay: GameplayEngine | None = None
                                 ) -> dict[str, Any] | None:
    """Return profile queries only; no purchase, alliance or mission award is implied."""
    kind, action = request.get("type"), request.get("action")
    if not isinstance(kind, str) or not isinstance(action, str) or (kind, action) not in ROUTES:
        return None
    if "cityId" in request:
        owned_city(request, session)
    payload: dict[str, Any]
    if action == "getUserRelation":
        payload = {"realtionList": []}  # Captured spelling, row 7875.
    elif action == "getUserRankValue":
        payload = {}  # Five observed successes have no global rank field, e.g. 6862.
    elif action == "getUserPh":
        # All 32 captures show this full-stamina profile. No combat drains it locally.
        payload = {"buyPh": 0, "maxUserPh": 100, "userPh": 100}
    elif action == "getUserDynamicFace":
        # GetUserDynamicFaceResponse::decode, physical Ghidra lines 604703 onward.
        async with engine._connection() as db:
            await engine._read(db, session.user_id, session.city_id)
            async with db.execute("SELECT money FROM users WHERE id=?", (session.user_id,)) as cursor:
                user = await cursor.fetchone()
        payload = {"money": user["money"], "needFinalBlow": 0, "hasFinalBlow": 0,
                   "userGoodsList": [], "userThingsList": [], "dynamicFaces": []}
    elif action == "getKingdomRoadInfo":
        group = integer(request.get("group"), "group", 0)
        if group != 27:
            raise EconomyError(900105, "Kingdom Road group has no captured query profile")
        # Rows 8470/8490 establish the schema. No paid campaign is active locally.
        payload = {"eventList": [], "myScore": 0, "rewardList": []}
    else:
        fixture = json.loads(files("empire_war_private_server").joinpath(
            "data/profile_views.json").read_text(encoding="utf-8"))
        buildings = (await gameplay.snapshot(session.user_id, session.city_id))["buildings"] if gameplay else []
        targets = []
        for source in fixture["targets"]:
            target = dict(source)
            matching = [b for b in buildings if b["bid"] == target["targetId"]]
            level = max((b["level"] for b in matching), default=0)
            # Only the three captured objectives are exposed. No alliance exists.
            count = sum(b["level"] >= target["targetLevel"] for b in matching) if target["targetType"] == 1 else 0
            target.update(currentValue=min(count, target["targetCount"]), thisLevel=level)
            targets.append(target)
        payload = {"getReward": 0, "targetList": targets, "rewardList": fixture["rewards"]}
    return {"type": kind, "action": action, "ret": 0, **payload}
