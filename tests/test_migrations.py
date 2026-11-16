"""State and audit schema evolution is one atomic, non-destructive operation."""

from importlib.resources import files
import json
from pathlib import Path

import aiosqlite
import pytest

from empire_war_private_server.economy import resource_engine
from empire_war_private_server.economy.resource_engine import CityResource, ResourceEngine


async def versions(engine):
    result = []
    for path in (engine.database, engine.audit_database):
        async with aiosqlite.connect(path) as db:
            async with db.execute("PRAGMA user_version") as cursor:
                result.append((await cursor.fetchone())[0])
    return result


async def v1_engine(tmp_path):
    engine = ResourceEngine(tmp_path / "state.db", tmp_path / "audit.db")
    async with engine._connection() as db:
        await db.executescript(files("empire_war_private_server").joinpath("migrations/001_sqlite.sql").read_text())
    await engine.seed_synthetic_user("legacy-local", "password", CityResource(wood=1234, people=7))
    return engine


async def test_upgrade_preserves_v1_accounts_balances_and_audit(tmp_path):
    engine = await v1_engine(tmp_path)
    assert await versions(engine) == [1, 1]
    before = await engine.get_resources(1, 1)
    await engine.initialize()
    await engine.initialize()
    assert await versions(engine) == [5, 5]
    assert await engine.get_resources(1, 1) == before
    assert await engine.authenticate("legacy-local", "password") == (1, 1)
    async with engine._connection() as db:
        async with db.execute("SELECT COUNT(*) FROM audit.economy_events") as cur:
            assert (await cur.fetchone())[0] == 1
        async with db.execute("SELECT money FROM users") as cur:
            assert (await cur.fetchone())[0] == 0
        async with db.execute("SELECT COUNT(*) FROM buildings") as cur:
            assert (await cur.fetchone())[0] == 0


@pytest.mark.parametrize("mode", ["future", "mismatch", "missing_audit", "unowned_audit"])
async def test_refuse_incompatible_pair_without_modifying_state(tmp_path, mode):
    engine = await v1_engine(tmp_path)
    if mode == "missing_audit":
        engine.audit_database.unlink()
    else:
        async with aiosqlite.connect(engine.audit_database) as db:
            sql = {"future": "PRAGMA user_version=6", "mismatch": "PRAGMA user_version=2",
                   "unowned_audit": "PRAGMA application_id=42"}[mode]
            await db.execute(sql)
    original = engine.database.read_bytes()
    with pytest.raises(ValueError):
        await engine.initialize()
    assert engine.database.read_bytes() == original
    if mode == "missing_audit":
        assert not engine.audit_database.exists()


async def test_failed_migration_rolls_back_ddl_versions_and_data(tmp_path, monkeypatch):
    engine = await v1_engine(tmp_path)
    original_files = resource_engine.files
    class BrokenMigration:
        def read_text(self, **kwargs):
            return original_files("empire_war_private_server").joinpath("migrations/002_gameplay.sql").read_text(**kwargs) + "\nINSERT INTO nonexistent_table VALUES (1);\n"
    class Package:
        def joinpath(self, path):
            return BrokenMigration() if path.endswith("002_gameplay.sql") else original_files("empire_war_private_server").joinpath(path)
    monkeypatch.setattr(resource_engine, "files", lambda _package: Package())
    with pytest.raises(aiosqlite.OperationalError):
        await engine.initialize()
    assert await versions(engine) == [1, 1]
    assert (await engine.get_resources(1, 1)).wood == 1234
    async with engine._connection() as db:
        async with db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='buildings'") as cur:
            assert (await cur.fetchone())[0] == 0
        async with db.execute("PRAGMA table_info(users)") as cur:
            assert "money" not in {row[1] for row in await cur.fetchall()}
    monkeypatch.setattr(resource_engine, "files", original_files)
    await engine.initialize()
    assert await versions(engine) == [5, 5]


async def test_fresh_pair_has_matching_versions_and_unique_active_queue(tmp_path):
    engine = ResourceEngine(tmp_path / "state.db", tmp_path / "audit.db")
    await engine.initialize()
    assert await versions(engine) == [5, 5]
    async with engine._connection() as db:
        async with db.execute("SELECT name FROM sqlite_master WHERE type='index'") as cur:
            assert "draft_one_active_per_building" in {row[0] for row in await cur.fetchall()}


@pytest.mark.parametrize("source_version", [1, 2])
@pytest.mark.parametrize("timestamp,expected", [
    (1_800_000_000_987, 1_800_000_000), (1_800_000_000, 1_800_000_000), (0, 0)])
async def test_wire_timestamp_migration_is_audited_once(tmp_path, source_version, timestamp, expected):
    engine = await v1_engine(tmp_path)
    async with engine._connection() as db:
        if source_version == 2:
            await db.executescript(files("empire_war_private_server").joinpath("migrations/002_gameplay.sql").read_text())
        await db.execute("UPDATE cities SET lastupdate=? WHERE cid=1", (timestamp,))
        async with db.execute("SELECT * FROM audit.economy_events ORDER BY id") as cur:
            old_events = [tuple(row) for row in await cur.fetchall()]
    await engine.initialize()
    await engine.initialize()
    assert await versions(engine) == [5, 5]
    assert (await engine.get_resources(1, 1)).lastupdate == expected
    async with engine._connection() as db:
        async with db.execute("SELECT * FROM audit.economy_events ORDER BY id") as cur:
            events = await cur.fetchall()
        assert [tuple(row) for row in events[:len(old_events)]] == old_events
        migrated = [row for row in events if row["action"] == "migrate_lastupdate_seconds"]
        assert len(migrated) == int(timestamp != expected)
        if migrated:
            assert json.loads(migrated[0]["before_json"]) == {"lastupdate": timestamp}
            assert json.loads(migrated[0]["after_json"]) == {"lastupdate": expected}
            assert migrated[0]["created_ms"] >= 1_000_000_000_000


async def test_timestamp_migration_audit_failure_rolls_back_pair(tmp_path):
    engine = await v1_engine(tmp_path)
    async with engine._connection() as db:
        await db.executescript(files("empire_war_private_server").joinpath("migrations/002_gameplay.sql").read_text())
        await db.execute("UPDATE cities SET lastupdate=1800000000987 WHERE cid=1")
        await db.execute("CREATE TRIGGER audit.reject_time_migration BEFORE INSERT ON economy_events WHEN NEW.action='migrate_lastupdate_seconds' BEGIN SELECT RAISE(ABORT,'fixture'); END")
    with pytest.raises(aiosqlite.IntegrityError):
        await engine.initialize()
    assert await versions(engine) == [2, 2]
    assert (await engine.get_resources(1, 1)).lastupdate == 1_800_000_000_987
    async with engine._connection() as db:
        async with db.execute("SELECT COUNT(*) FROM audit.economy_events") as cur:
            assert (await cur.fetchone())[0] == 1
