"""Authenticated adapters for the captured Market action namespace."""

from __future__ import annotations

from typing import Any

from ..auth.session_manager import Session
from ..economy.market_engine import MarketEngine
from ..protocol.city_position import owned_city


async def dispatch_market(request: dict[str, Any], session: Session,
                          market: MarketEngine, *,
                          frame_metadata: dict[str, int] | None = None) -> dict[str, Any] | None:
    """Ownership precedes arithmetic, durable logging and failure simulation."""
    if request.get("type") != "Market" or request.get("action") not in (
            "sellToUser", "doGetCityTrade", "getUserSellData", "cancelSell"):
        return None
    city_id = owned_city(request, session)
    if request["action"] == "sellToUser":
        return await market.sell_to_user(session.user_id, city_id, request,
                                         frame_metadata=frame_metadata, wire_city_id=session.wire_city_id)
    if request["action"] == "doGetCityTrade":
        return await market.own_trades(session.user_id, city_id)
    if request["action"] == "cancelSell":
        return await market.cancel_sell(session.user_id, city_id, request.get("tradeId"))
    return await market.user_sell_data(session.user_id, city_id)
