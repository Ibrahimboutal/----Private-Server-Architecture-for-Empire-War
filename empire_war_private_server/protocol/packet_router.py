"""Route captured (type, action) pairs, enforcing connection session context."""

from __future__ import annotations

from typing import Any

from ..auth.session_manager import ConnectionContext, SessionManager
from ..economy.resource_engine import EconomyError, ResourceEngine
from ..handlers.do_login import do_login
from ..handlers.synch_city_resource import synch_city_resource


class PacketRouter:
    def __init__(self, engine: ResourceEngine, sessions: SessionManager) -> None:
        self.engine, self.sessions = engine, sessions

    async def dispatch(self, request: dict[str, Any], context: ConnectionContext) -> dict[str, Any]:
        """Return explicit local errors for unsupported/unverified semantics."""
        action, kind = request.get("action"), request.get("type")
        envelope = {"action": action, "type": kind}
        try:
            if (kind, action) == ("User", "doLogin"):
                return await do_login(request, context, self.engine, self.sessions)
            session = self.sessions.get(context.session_id)
            if session is None:
                raise EconomyError(900101, "Valid local session required")
            if (kind, action) == ("City", "synchCityResource"):
                return await synch_city_resource(request, session, self.engine)
            raise EconomyError(900104, "Action is not implemented in Phase 1")
        except EconomyError as exc:
            return {**envelope, "ret": exc.ret, "msg": str(exc)}
