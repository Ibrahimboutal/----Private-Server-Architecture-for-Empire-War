"""Migration v4 preserves existing gameplay and evolves both files atomically."""

from importlib.resources import files
from pathlib import Path

import aiosqlite
import pytest

from empire_war_private_server.economy.resource_engine import CityResource, ResourceEngine


async def prior_engine(tmp_path: Path) -> ResourceEngine:
    engine = ResourceEngine(tmp_path / "state.db", tmp_path / "audit.db")
    async with engine._connection() as db:
        for name in ("001_sqlite.sql", "002_gameplay.sql", "003_wire_time_seconds.sql"):
            await db.executescript(files("empire_war_private_server").joinpath(f"migrations/{name}").read_text(encoding="utf-8"))
    await engine.seed_synthetic_user("market-migration-fixture", "local-only", CityResource(
        wood=1234, food=2345, people=10, lastupdate=1_800_000_000))
    async with engine._transaction() as db:
        await db.execute("UPDATE users SET money=91 WHERE id=1")
        await db.execute("INSERT INTO buildings(id,cid,bid,level,xy,inner) VALUES (1,1,9,1,144,1)")
        await db.execute("INSERT INTO troops(cid,sid,count) VALUES (1,2,7)")
        await db.execute("INSERT INTO scenario_cities(cid,scenario_id) VALUES (1,'captured-phase2-v1')")
        await db.execute("INSERT INTO draft_queues(cid,building_id,sid,soldier_count,duration_s,state,start_s,end_s,audit_id) VALUES (1,1,2,1,21,1,1800000000,1800000021,1)")
    return engine


async def versions(engine: ResourceEngine) -> list[int]:
    async with engine._connection() as db:
        result = []
        for schema in ("main", "audit"):
            async with db.execute(f"PRAGMA {schema}.user_version") as cursor:
                result.append((await cursor.fetchone())[0])
        return result


async def old_rows(engine: ResourceEngine) -> dict[str, list[tuple]]:
    async with engine._connection() as db:
        result = {}
        for table in ("users", "cities", "buildings", "troops", "draft_queues", "scenario_cities", "audit.economy_events"):
            async with db.execute(f"SELECT * FROM {table} ORDER BY rowid") as cursor:
                result[table] = [tuple(row) for row in await cursor.fetchall()]
        return result


async def test_v3_market_migration_preserves_accounts_gameplay_and_audit(tmp_path):
    engine = await prior_engine(tmp_path)
    assert await versions(engine) == [3, 3]
    before = await old_rows(engine)
    await engine.initialize()
    await engine.initialize()
    assert await versions(engine) == [5, 5]
    assert await old_rows(engine) == before
    assert await engine.authenticate("market-migration-fixture", "local-only") == (1, 1)
    async with engine._connection() as db:
        for table in ("market_listings", "audit.market_events"):
            async with db.execute(f"SELECT COUNT(*) FROM {table}") as cursor:
                assert (await cursor.fetchone())[0] == 0
        async with db.execute("SELECT singleton,consecutive_overflows,breaker_tripped FROM market_research_state") as cursor:
            assert [tuple(row) for row in await cursor.fetchall()] == [(1, 0, 0)]


async def test_v4_reinitialization_preserves_persisted_breaker(tmp_path):
    engine = await prior_engine(tmp_path)
    await engine.initialize()
    async with engine._transaction() as db:
        await db.execute("UPDATE market_research_state SET consecutive_overflows=3,breaker_tripped=1 WHERE singleton=1")
    await engine.initialize()
    async with engine._connection() as db:
        async with db.execute("SELECT consecutive_overflows,breaker_tripped FROM market_research_state WHERE singleton=1") as cursor:
            assert tuple(await cursor.fetchone()) == (3, 1)


async def test_audit_schema_collision_rolls_back_main_market_migration(tmp_path):
    engine = await prior_engine(tmp_path)
    before = await old_rows(engine)
    # A conflicting audit object makes CREATE TABLE fail after main DDL. The
    # migration must leave that object and the previous schema pair untouched.
    async with engine._connection() as db:
        await db.execute("CREATE TABLE audit.market_events(sentinel TEXT NOT NULL)")
        await db.execute("INSERT INTO audit.market_events VALUES ('preserve fixture')")
    with pytest.raises(aiosqlite.OperationalError):
        await engine.initialize()
    assert await versions(engine) == [3, 3]
    assert await old_rows(engine) == before
    async with engine._connection() as db:
        async with db.execute("SELECT name FROM sqlite_master WHERE name IN ('market_listings','market_research_state','market_listings_city')") as cursor:
            assert await cursor.fetchall() == []
        async with db.execute("SELECT sentinel FROM audit.market_events") as cursor:
            assert (await cursor.fetchone())[0] == "preserve fixture"


@pytest.mark.parametrize("user_id,cid,count", [(99, 1, 1), (1, 99, 1), (1, 1, 0)])
async def test_market_listing_ownership_references_and_count_constraints(tmp_path, user_id, cid, count):
    engine = await prior_engine(tmp_path)
    await engine.initialize()
    with pytest.raises(aiosqlite.IntegrityError):
        async with engine._transaction() as db:
            await db.execute("INSERT INTO market_listings(user_id,cid,res_type,res_count,gold,hour,unit_price,generate_time_s,limit_time_s,trade_json) VALUES (?,?,1,?,1,1,1,1800000000,1800003600,'{}')", (user_id, cid, count))
    async with engine._connection() as db:
        async with db.execute("SELECT COUNT(*) FROM market_listings") as cursor:
            assert (await cursor.fetchone())[0] == 0
