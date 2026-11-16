"""Atomicity, real concurrent connections, ownership and audit rollback tests."""

import asyncio
from dataclasses import fields
import json
import re
from pathlib import Path

import aiosqlite
import pytest

from empire_war_private_server.economy.resource_engine import (
    CityResource, EconomyError, ResourceEngine, load_troop_costs,
)
from empire_war_private_server.economy.time_util import CostDomainError
from empire_war_private_server.economy import resource_engine


@pytest.fixture
async def engine(tmp_path):
    ledger = ResourceEngine(tmp_path / "state.db", tmp_path / "audit.db")
    await ledger.initialize()
    await ledger.seed_synthetic_user("lab", "local-password", CityResource(
        food=1000, wood=1000, iron=1000, rock=1000, gold=1000, people=100))
    return ledger


async def count_rows(path, table):
    async with aiosqlite.connect(path) as db:
        async with db.execute(f"SELECT COUNT(*) FROM {table}") as cursor:
            return (await cursor.fetchone())[0]


async def test_all_resources_checked_before_any_deduction(engine):
    before = await engine.get_resources(1, 1)
    with pytest.raises(EconomyError) as error:
        await engine.validate_and_deduct_resources(1, 1, {"food": 100, "wood": 1001})
    assert error.value.ret == 13003
    assert await engine.get_resources(1, 1) == before
    assert await count_rows(engine.audit_database, "economy_events") == 1  # Seed only.


async def test_deduction_wire_clock_uses_seconds_and_audit_uses_milliseconds(engine, monkeypatch):
    monkeypatch.setattr(resource_engine.time, "time_ns", lambda: 1_800_000_000_987_654_321)
    result = await engine.validate_and_deduct_resources(1, 1, {"wood": 1})
    assert result.resources.lastupdate == 1_800_000_000
    async with engine._connection() as db:
        async with db.execute("SELECT created_ms FROM audit.economy_events WHERE id=?", (result.audit_id,)) as cur:
            assert (await cur.fetchone())[0] == 1_800_000_000_987


async def test_concurrent_deductions_serialize_without_overspend(engine):
    # Separate engine objects and connections: no process-local lock hides races.
    second = ResourceEngine(engine.database, engine.audit_database)
    results = await asyncio.gather(*[
        (engine if i % 2 else second).validate_and_deduct_resources(1, 1, {"wood": 700, "food": 100})
        for i in range(10)
    ], return_exceptions=True)
    assert sum(not isinstance(r, Exception) for r in results) == 1
    assert all(not isinstance(r, Exception) or isinstance(r, EconomyError) and r.ret == 13003 for r in results)
    resources = await engine.get_resources(1, 1)
    assert (resources.wood, resources.food) == (300.0, 900.0)
    assert await count_rows(engine.audit_database, "economy_events") == 2


async def test_audit_failure_rolls_back_state(engine):
    async with aiosqlite.connect(engine.audit_database) as db:
        await db.execute("CREATE TRIGGER reject_audit BEFORE INSERT ON economy_events BEGIN SELECT RAISE(ABORT,'test failure'); END")
        await db.commit()
    before = await engine.get_resources(1, 1)
    with pytest.raises(aiosqlite.IntegrityError):
        await engine.validate_and_deduct_resources(1, 1, {"food": 100})
    assert await engine.get_resources(1, 1) == before


async def test_reservation_failure_rolls_back_costs_and_audit(engine):
    async with aiosqlite.connect(engine.database) as db:
        await db.execute("CREATE TRIGGER reject_reservation BEFORE INSERT ON training_reservations BEGIN SELECT RAISE(ABORT,'test failure'); END")
        await db.commit()
    before = await engine.get_resources(1, 1)
    with pytest.raises(aiosqlite.IntegrityError):
        await engine.reserve_troops(1, 1, 1, 1)
    assert await engine.get_resources(1, 1) == before
    assert await count_rows(engine.audit_database, "economy_events") == 1


async def test_cancellation_rolls_back_state_and_audit(engine, monkeypatch):
    reached = asyncio.Event()
    original = engine._audit

    async def hold_after_audit(*args, **kwargs):
        result = await original(*args, **kwargs)
        reached.set()
        await asyncio.Future()
        return result

    monkeypatch.setattr(engine, "_audit", hold_after_audit)
    before = await engine.get_resources(1, 1)
    task = asyncio.create_task(engine.validate_and_deduct_resources(1, 1, {"wood": 100}))
    await asyncio.wait_for(reached.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await engine.get_resources(1, 1) == before
    assert await count_rows(engine.audit_database, "economy_events") == 1


async def test_costs_lookup_and_no_request_deduplication(engine):
    await engine.reserve_troops(1, 1, 1, 2)
    await engine.reserve_troops(1, 1, 1, 2)
    resource = await engine.get_resources(1, 1)
    assert (resource.food, resource.wood, resource.iron, resource.people) == (800, 400, 960, 96)
    assert await count_rows(engine.database, "training_reservations") == 2
    async with aiosqlite.connect(engine.audit_database) as db:
        async with db.execute("SELECT costs_json FROM economy_events WHERE action='reserve_troops'") as cursor:
            for row in await cursor.fetchall():
                assert json.loads(row[0])["wood"] == 300


@pytest.mark.parametrize("costs", [{"food": -1}, {"food": float("inf")},
    {"food": float("nan")}, {"food": True}, {"food": 10**1000},
    {"missing": 1}, {"people": 0.5}, {}])
async def test_invalid_costs_do_not_mutate(engine, costs):
    before = await engine.get_resources(1, 1)
    with pytest.raises(EconomyError):
        await engine.validate_and_deduct_resources(1, 1, costs)
    assert await engine.get_resources(1, 1) == before


async def test_ownership_and_unknown_sid(engine):
    with pytest.raises(EconomyError) as error:
        await engine.validate_and_deduct_resources(2, 1, {"wood": 100})
    assert error.value.ret == 900102
    for sid, count in [(999999, 1), (1, -1), (True, 1), (1, 2**31-1)]:
        with pytest.raises(EconomyError):
            await engine.reserve_troops(1, 1, sid, count)


async def test_seed_never_resets_existing_resources(engine):
    await engine.validate_and_deduct_resources(1, 1, {"wood": 100})
    await engine.seed_synthetic_user("lab", "local-password", CityResource(wood=99999))
    assert (await engine.get_resources(1, 1)).wood == 900
    with pytest.raises(ValueError):
        await engine.seed_synthetic_user("lab", "different-password", CityResource())


async def test_unowned_database_is_not_modified(tmp_path):
    database = tmp_path / "capture.db"
    async with aiosqlite.connect(database) as db:
        await db.execute("CREATE TABLE captured_packets(id INTEGER)")
        await db.commit()
    original = database.read_bytes()
    ledger = ResourceEngine(database, tmp_path / "audit.db")
    with pytest.raises(ValueError, match="not owned"):
        await ledger.initialize()
    assert database.read_bytes() == original
    assert not ledger.audit_database.exists()


def test_resource_fields_match_the_actual_ghidra_excerpt():
    source = Path(__file__).resolve().parents[1] / "GhidraProjects/reports/gold_logic_focus.c"
    if not source.exists():
        pytest.skip("optional local Ghidra evidence is unavailable")
    text = source.read_text(encoding="utf-8")
    block = text[text.index("void __thiscall MemCityResource_decode"):text.index("* FUNCTION: MemSummonHeroCondition_decode")]
    decoded_names = set(re.findall(r'param_2,"(\w+)"', block))
    assert {f.name for f in fields(CityResource)} == decoded_names | {"vacation"}


def test_captured_base_costs_and_injected_tables():
    costs = load_troop_costs()
    assert len(costs) == 99
    assert costs[1] == {"food": 50, "wood": 150, "iron": 10, "rock": 0, "gold": 0, "people": 1}
    with pytest.raises(ValueError):
        ResourceEngine(Path("unused.db"), Path("unused_audit.db"), {1: {"food": 1}})


async def test_reopen_owned_schema(engine):
    reopened = ResourceEngine(engine.database, engine.audit_database)
    await reopened.initialize()
    assert await reopened.get_resources(1, 1) == await engine.get_resources(1, 1)


def test_integer_to_float_field_roundtrip_at_signed_boundary():
    for value in (0, 2**24 + 1, 2**31 - 128, -(2**31)):
        snapshot = CityResource(foodArmyUseRate=value)
        assert CityResource(**snapshot.to_wire()) == snapshot
    with pytest.raises(CostDomainError):
        CityResource(foodArmyUseRate=2**31 - 1)
