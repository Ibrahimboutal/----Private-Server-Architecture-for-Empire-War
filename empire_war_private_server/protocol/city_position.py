"""Represent the seeded city on the native map while retaining its SQLite key."""

from typing import Any

from ..auth.session_manager import Session
from ..economy.resource_engine import EconomyError

# The client requires both cid // 1000 and cid % 1000 in 1..500.
# Research City occupies the synthetic map square (1, 1).
RESEARCH_MAP_CID = 1001


def owned_city(request: dict[str, Any], session: Session) -> int:
    """Resolve only the city advertised to this authenticated connection."""
    requested = request.get("cityId")
    if type(requested) is not int or requested != session.wire_city_id:
        raise EconomyError(900102, "City is not owned by this session")
    return session.city_id


def project_city_fields(payload: dict[str, Any], session: Session) -> dict[str, Any]:
    """Project owned city references in a freshly constructed response."""
    if session.wire_city_id == session.city_id:
        return payload

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for entry in node:
                visit(entry)
        elif isinstance(node, dict):
            for key, value in node.items():
                if key in ("cid", "lastcid") and type(value) is int and value == session.city_id:
                    node[key] = session.wire_city_id
                elif not key.startswith("cfg"):
                    visit(value)

    visit(payload)
    return payload
