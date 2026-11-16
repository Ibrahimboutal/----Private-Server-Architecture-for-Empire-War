"""Connection-session lifetime and isolation without external dependencies."""

from types import SimpleNamespace
from uuid import UUID

import pytest

from empire_war_private_server.auth import session_manager
from empire_war_private_server.auth.session_manager import ConnectionContext, SessionManager


def test_unique_uuid_sessions_and_independent_connection_contexts() -> None:
    manager = SessionManager()
    first = manager.create(1, 10)
    second = manager.create(1, 10)
    assert UUID(first.session_id).version == 4
    assert UUID(second.session_id).version == 4
    assert first.session_id != second.session_id
    assert manager.get(first.session_id) == first
    assert manager.get(second.session_id) == second
    context_a, context_b = ConnectionContext(first.session_id), ConnectionContext()
    assert context_b.session_id is None
    manager.revoke(context_a.session_id)
    assert manager.get(first.session_id) is None
    assert manager.get(second.session_id) == second
    manager.revoke(None)
    manager.revoke(first.session_id)
    assert manager.get(None) is None
    assert manager.get("unknown-session") is None


def test_absolute_expiry_at_boundary_and_reads_do_not_extend_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [100.0]
    monkeypatch.setattr(session_manager, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    manager = SessionManager(ttl_seconds=10)
    session = manager.create(1, 1)
    assert session.expires_at == 110.0
    clock[0] = 109.999
    assert manager.get(session.session_id) == session
    clock[0] = 110.0
    assert manager.get(session.session_id) is None
    clock[0] = 109.0
    assert manager.get(session.session_id) is None  # Expiry also evicts the entry.


def test_capacity_and_pruning_of_unread_expired_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [0.0]
    monkeypatch.setattr(session_manager, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    manager = SessionManager(ttl_seconds=1, max_sessions=1)
    old = manager.create(1, 1)
    with pytest.raises(ValueError, match="capacity"):
        manager.create(1, 1)
    clock[0] = 1.0
    new = manager.create(1, 1)
    assert manager.get(old.session_id) is None
    assert manager.get(new.session_id) == new
    manager.revoke(new.session_id)
    assert manager.create(2, 2).user_id == 2


@pytest.mark.parametrize("options", [
    {"ttl_seconds": 0}, {"ttl_seconds": 3601}, {"ttl_seconds": True},
    {"ttl_seconds": 1.5}, {"max_sessions": 0}, {"max_sessions": False},
])
def test_session_configuration_rejects_invalid_limits(options: dict) -> None:
    with pytest.raises(ValueError):
        SessionManager(**options)
