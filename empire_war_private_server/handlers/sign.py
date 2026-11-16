"""Durable synthetic daily sign-in actions."""

from __future__ import annotations

import time
from typing import Any

from ..auth.session_manager import Session
from ..economy.resource_engine import EconomyError, ResourceEngine
from ..protocol.city_position import owned_city


def _day() -> int:
    return int(time.time() // 86400)


async def dispatch_sign(request: dict[str, Any], session: Session,
                        engine: ResourceEngine) -> dict[str, Any] | None:
    kind, action = request.get("type"), request.get("action")
    if action not in ("signToday", "getSignInfo") or kind not in ("Sign", "Event"):
        return None
    if action == "getSignInfo" and kind != "Sign":
        return None
    city_id = owned_city(request, session) if "cityId" in request else session.city_id
    today = _day()
    async with engine._transaction() as db:
        await engine._read(db, session.user_id, city_id)
        async with db.execute("SELECT sign_count,last_day FROM daily_signins WHERE cid=?",
                              (city_id,)) as cursor:
            row = await cursor.fetchone()
        if row is None:
            await db.execute("INSERT INTO daily_signins(cid) VALUES (?)", (city_id,))
            count, last_day = 0, None
        else:
            count, last_day = row["sign_count"], row["last_day"]
        if action == "signToday":
            if last_day != today:
                count += 1
                await db.execute("UPDATE daily_signins SET sign_count=?,last_day=? WHERE cid=?",
                                 (count, today, city_id))
                await engine._audit(db, session.user_id, city_id, "signToday",
                                    {"signCount": count - 1, "lastDay": last_day},
                                    {"signCount": count, "lastDay": today}, {})
            payload = {"type": kind, "action": action, "ret": 0}
            if kind == "Sign":
                payload["signCount"] = count
            return payload
        return {
            "type": "Sign", "action": "getSignInfo", "ret": 0,
            "countRewardList": [], "gameTime": str(today),
            "userSignInfo": {"maxSignCount": 30, "signCount": count,
                             "todayIsSign": last_day == today, "uid": session.user_id},
        }
