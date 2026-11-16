"""Route captured (type, action) pairs, enforcing connection session context."""

from __future__ import annotations

from typing import Any

from ..auth.session_manager import ConnectionContext, SessionManager
from ..economy.resource_engine import EconomyError, ResourceEngine
from ..economy.gameplay_engine import GameplayEngine
from ..economy.market_engine import MarketEngine
from ..economy.online_rewards import OnlineRewardEngine
from ..handlers.do_login import do_login
from ..handlers.gameplay import dispatch_gameplay
from ..handlers.synch_city_resource import synch_city_resource
from ..handlers.sell_to_user import dispatch_market
from ..handlers.get_server_version import get_server_version
from ..handlers.client_startup import dispatch_client_startup
from ..handlers.city_info import read_city_state
from ..handlers.profile_views import dispatch_profile_views
from .city_position import RESEARCH_MAP_CID, owned_city, project_city_fields


class PacketRouter:
    def __init__(self, engine: ResourceEngine, sessions: SessionManager,
                 gameplay: GameplayEngine | None = None, market: MarketEngine | None = None, *,
                 synthetic_credentials: tuple[str, str] | None = None) -> None:
        self.engine, self.sessions = engine, sessions
        self.gameplay = gameplay
        self.market = market
        self.online_rewards = OnlineRewardEngine(engine)
        self.synthetic_credentials = synthetic_credentials

    async def dispatch(self, request: dict[str, Any], context: ConnectionContext, *,
                       frame_metadata: dict[str, int] | None = None) -> dict[str, Any]:
        """Return explicit local errors for unsupported/unverified semantics."""
        action, kind = request.get("action"), request.get("type")
        envelope = {"action": action, "type": kind}
        try:
            if (kind, action) == ("User", "getServerVersion"):
                return get_server_version(request)
            if (kind, action) == ("User", "doLogin"):
                return await do_login(request, context, self.engine, self.sessions, self.gameplay,
                                      synthetic_credentials=self.synthetic_credentials)
            session = self.sessions.get(context.session_id)
            if session is None:
                raise EconomyError(900101, "Valid local session required")
            if (kind, action) == ("City", "dummyChangePosition"):
                user_id = request.get("userId")
                if type(user_id) is not int or user_id != session.user_id:
                    raise EconomyError(900102, "User is not owned by this session")
                if session.city_id == 1 and session.map_city_id is None:
                    session = self.sessions.position_city(session, RESEARCH_MAP_CID)
                return {**envelope, "ret": 0, "targetCid": session.wire_city_id}
            if (kind, action) == ("User", "getCityInfo"):
                owned_city(request, session)
                state, _ = await read_city_state(session, self.engine, self.gameplay)
                return project_city_fields({**envelope, "ret": 0, "exception": 0, **state}, session)
            if kind == "Event" and action in ("getOnlineRewardInfo", "getOnlineReward"):
                if action == "getOnlineReward" or "cityId" in request:
                    owned_city(request, session)
                if action == "getOnlineReward":
                    payload = await self.online_rewards.claim(session.user_id, session.city_id)
                else:
                    payload = await self.online_rewards.info(session.user_id, session.city_id)
                return {**envelope, "ret": 0, **payload}
            startup = dispatch_client_startup(request, session)
            if startup is not None:
                if action == "getLoginShowInfo":
                    reward = await self.online_rewards.info(session.user_id, session.city_id, include_catalog=False)
                    startup.update(onlineStep=reward["step"], onlineTimeLeft=reward["timeLeft"],
                                   nextOnlineRewardTime=reward["nextOnlineRewardTime"])
                return startup
            response = await dispatch_profile_views(request, session, self.engine, self.gameplay)
            if response is not None:
                return project_city_fields(response, session)
            if (kind, action) == ("City", "synchCityResource"):
                response = await synch_city_resource(request, session, self.engine, self.gameplay)
                return project_city_fields(response, session)
            if self.market is not None:
                response = await dispatch_market(request, session, self.market, frame_metadata=frame_metadata)
                if response is not None:
                    return project_city_fields(response, session)
            if self.gameplay is not None:
                response = await dispatch_gameplay(request, session, self.gameplay)
                if response is not None:
                    if action == "getBuildingState" and self.market is not None:
                        response["market"]["myCount"] = await self.market.listing_count(session.user_id, session.city_id)
                    return project_city_fields(response, session)
            raise EconomyError(900104, "Action is not implemented by this research server")
        except EconomyError as exc:
            return {**envelope, "ret": exc.ret, "msg": str(exc)}
