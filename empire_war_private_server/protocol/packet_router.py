"""Route captured (type, action) pairs, enforcing connection session context."""

from __future__ import annotations

from typing import Any

from ..auth.session_manager import ConnectionContext, SessionManager
from ..economy.resource_engine import EconomyError, ResourceEngine
from ..economy.gameplay_engine import GameplayEngine
from ..economy.market_engine import MarketEngine
from ..handlers.do_login import do_login
from ..handlers.gameplay import dispatch_gameplay
from ..handlers.synch_city_resource import synch_city_resource
from ..handlers.sell_to_user import dispatch_market


class PacketRouter:
    def __init__(self, engine: ResourceEngine, sessions: SessionManager,
                 gameplay: GameplayEngine | None = None, market: MarketEngine | None = None) -> None:
        self.engine, self.sessions = engine, sessions
        self.gameplay = gameplay
        self.market = market

    async def dispatch(self, request: dict[str, Any], context: ConnectionContext, *,
                       frame_metadata: dict[str, int] | None = None) -> dict[str, Any]:
        """Return explicit local errors for unsupported/unverified semantics."""
        action, kind = request.get("action"), request.get("type")
        envelope = {"action": action, "type": kind}
        try:
            if (kind, action) == ("User", "doLogin"):
                return await do_login(request, context, self.engine, self.sessions, self.gameplay)
            session = self.sessions.get(context.session_id)
            if session is None:
                raise EconomyError(900101, "Valid local session required")
            if (kind, action) == ("City", "synchCityResource"):
                return await synch_city_resource(request, session, self.engine, self.gameplay)
            if self.market is not None:
                response = await dispatch_market(request, session, self.market, frame_metadata=frame_metadata)
                if response is not None:
                    return response
            if self.gameplay is not None:
                response = await dispatch_gameplay(request, session, self.gameplay)
                if response is not None:
                    if action == "getBuildingState" and self.market is not None:
                        response["market"]["myCount"] = await self.market.listing_count(session.user_id, session.city_id)
                    return response
            raise EconomyError(900104, "Action is not implemented by this research server")
        except EconomyError as exc:
            return {**envelope, "ret": exc.ret, "msg": str(exc)}
