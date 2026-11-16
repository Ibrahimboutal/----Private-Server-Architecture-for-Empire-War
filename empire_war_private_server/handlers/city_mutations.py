"""Authenticated city mutation adapters with bounded captured semantics."""

from __future__ import annotations

from typing import Any

from ..auth.session_manager import Session
from ..economy.resource_engine import EconomyError, ResourceEngine
from ..protocol.city_position import owned_city


async def dispatch_city_mutations(request: dict[str, Any], session: Session,
                                  engine: ResourceEngine) -> dict[str, Any] | None:
    if request.get("type") != "City" or request.get("action") != "changeTax":
        return None
    city_id = owned_city(request, session)
    new_tax = request.get("newTax")
    if type(new_tax) is not int or not 0 <= new_tax <= 100:
        raise EconomyError(900103, "Invalid newTax")
    async with engine._transaction() as db:
        before = await engine._read(db, session.user_id, city_id)
        await db.execute("UPDATE cities SET tax=? WHERE cid=? AND user_id=?",
                         (new_tax, city_id, session.user_id))
        after = await engine._read(db, session.user_id, city_id)
        await engine._audit(db, session.user_id, city_id, "changeTax",
                            {"tax": before.tax}, {"tax": after.tax}, {})
    return {"type": "City", "action": "changeTax", "ret": 0}
