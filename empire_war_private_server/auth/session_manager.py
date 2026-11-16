"""UUID sessions, with an explicit one-hour TTL and no request deduplication.

Observed gameplay requests carry no UUID. The server validates the session
attached to the accepted TCP connection rather than adding required wire fields.
The cache is process-local in Phase 1; restarting intentionally invalidates it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import time
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class Session:
    session_id: str
    user_id: int
    city_id: int
    expires_at: float
    map_city_id: int | None = None

    @property
    def wire_city_id(self) -> int:
        return self.city_id if self.map_city_id is None else self.map_city_id


class SessionManager:
    """Bounded cache used only on one asyncio event loop."""

    def __init__(self, ttl_seconds: int = 3600, max_sessions: int = 128) -> None:
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
            raise ValueError("session TTL must be 1..3600 seconds")
        if type(max_sessions) is not int or max_sessions < 1:
            raise ValueError("max_sessions must be positive")
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self._sessions: dict[str, Session] = {}

    def create(self, user_id: int, city_id: int, *, map_city_id: int | None = None) -> Session:
        now = time.monotonic()
        self._sessions = {key: value for key, value in self._sessions.items() if value.expires_at > now}
        if len(self._sessions) >= self.max_sessions:
            raise ValueError("session capacity reached")
        session = Session(str(uuid4()), user_id, city_id, now + self.ttl_seconds, map_city_id)
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str | None) -> Session | None:
        if session_id is None:
            return None
        session = self._sessions.get(session_id)
        if session and session.expires_at <= time.monotonic():
            self.revoke(session_id)
            return None
        return session

    def revoke(self, session_id: str | None) -> None:
        if session_id is not None:
            self._sessions.pop(session_id, None)

    def position_city(self, session: Session, map_city_id: int) -> Session:
        """Keep the existing authenticated session and expiry during city placement."""
        placed = replace(session, map_city_id=map_city_id)
        self._sessions[session.session_id] = placed
        return placed


@dataclass(slots=True)
class ConnectionContext:
    session_id: str | None = None
