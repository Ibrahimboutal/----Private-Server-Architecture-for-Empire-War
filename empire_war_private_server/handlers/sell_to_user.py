"""Authenticated adapters for the captured Market action namespace."""

from __future__ import annotations

from typing import Any

from ..auth.session_manager import Session
from ..economy.market_engine import MarketEngine
from ..economy.resource_engine import EconomyError


async def dispatch_market(request: dict[str, Any], session: Session,
                          market: MarketEngine, *,
                          frame_metadata: dict[str, int] | None = None) -> dict[str, Any] | None:
    """Ownership precedes arithmetic, durable logging and failure simulation."""
    if request.get("type") != "Market" or request.get("action") not in (
            "sellToUser", "doGetCityTrade", "getUserSellData"):
        return None
    city_id = request.get("cityId")
    if type(city_id) is not int or city_id != session.city_id:
        raise EconomyError(900102, "City is not owned by this session")
    if request["action"] == "sellToUser":
        return await market.sell_to_user(session.user_id, city_id, request,
                                         frame_metadata=frame_metadata)
    if request["action"] == "doGetCityTrade":
        return await market.own_trades(session.user_id, city_id)
    return await market.user_sell_data(session.user_id, city_id)
