"""Synthetic login using the observed User/doLogin response field names."""

from __future__ import annotations

from importlib.resources import files
import json
import time
from typing import Any

from ..auth.session_manager import ConnectionContext, SessionManager
from ..economy.resource_engine import EconomyError, ResourceEngine
from ..economy.gameplay_engine import GameplayEngine
from ..economy.client_catalog import load_client_catalog, merge_scenario_catalog
from ..protocol.city_position import RESEARCH_MAP_CID, project_city_fields
from .city_info import read_city_state


async def do_login(request: dict[str, Any], context: ConnectionContext,
                   engine: ResourceEngine, sessions: SessionManager,
                   gameplay: GameplayEngine | None = None, *,
                   synthetic_credentials: tuple[str, str] | None = None) -> dict[str, Any]:
    """Log in locally; the opt-in client bridge always selects the seeded user.

    Bridging is a local test policy, not an implementation of official account
    authentication. Incoming account strings are validated, then discarded;
    neither the response nor persistence receives those values. The caller
    supplies configured synthetic credentials only when bridging is enabled.
    """
    sessions.revoke(context.session_id)
    context.session_id = None
    passport, password = request.get("passport"), request.get("password")
    # Native DoLoginRequest serializes strings even when no account string is
    # populated. The local bridge discards them, so empty strings are valid in
    # that mode. Strict authentication still requires nonempty credentials.
    if (not isinstance(passport, str) or not isinstance(password, str)
            or len(passport) > 128 or len(password) > 256):
        raise EconomyError(900101, "Synthetic credentials required")
    if synthetic_credentials is not None:
        passport, password = synthetic_credentials
    elif not passport or not password:
        raise EconomyError(900101, "Synthetic credentials required")
    identity = await engine.authenticate(passport, password)
    if identity is None:
        raise EconomyError(900101, "Invalid synthetic credentials")
    user_id, city_id = identity
    map_cid = RESEARCH_MAP_CID if city_id == 1 and request.get("phoneOs") == "android" else None
    session = sessions.create(user_id, city_id, map_city_id=map_cid)
    context.session_id = session.session_id
    # Every scalar in this shape was emptied by the offline fixture extractor.
    # These replacements are explicitly synthetic research fixture values.
    response = json.loads(files("empire_war_private_server").joinpath("data/login_shape.json").read_text(encoding="utf-8"))
    now_s = time.time_ns() // 1_000_000_000
    response.update(type="User", action="doLogin", ret=0,
                    gameUUID=session.session_id, serverTime=now_s, gameTime=now_s)
    response["user"].update(uid=user_id, lastcid=city_id, name="Research User",
                            passport=passport, ip="127.0.0.1", locale="en_US")
    state, money = await read_city_state(session, engine, gameplay)
    response.update(state)
    # Native login refreshes ConfigManager before entering the city. Return
    # the complete captured static catalog for those lookups; action handlers
    # continue to enforce only supported capture-derived gameplay scenarios.
    catalog = load_client_catalog()
    if gameplay is not None:
        catalog = merge_scenario_catalog(catalog, gameplay.login_configuration())
        response["user"]["money"] = money
    response.update(catalog)
    return project_city_fields(response, session)
