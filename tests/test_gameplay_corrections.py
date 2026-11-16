"""Captured upgrade effects, shortage boundaries and atomic completion failures."""

import asyncio
from dataclasses import dataclass
from pathlib import Path

import aiosqlite
import pytest

from empire_war_private_server.economy.gameplay_engine import GameplayEngine
from empire_war_private_server.economy.resource_engine import CityResource, EconomyError, ResourceEngine


QUARRY_SCENARIO = "captured-quarry-shortage-v1"


@dataclass
class Scenario:
    ledger: ResourceEngine
    game: GameplayEngine
    now: list[int]


async def make_scenario(path: Path, *, quarry: bool = False, wood: float | None = None,
                        premium_money: int = 100) -> Scenario:
    ledger = ResourceEngine(path / "state.db", path / "audit.db")
    await ledger.initialize()
    await ledger.seed_synthetic_user("correction-fixture", "local-test", CityResource(
        food=10000, wood=(178 if quarry else 10000) if wood is None else wood,
        iron=10000, rock=10000, gold=1234, people=1000, peopleMax=1000,
        peopleStable=900, peopleWorking=25, rockAdd=100, rockMax=50000))
    now = [1_800_000_000]
    game = GameplayEngine(ledger, scenario_id=QUARRY_SCENARIO if quarry else "captured-phase2-v1",
                          clock=lambda: now[0])
    await game.seed_city(1, 1, premium_money=premium_money)
    return Scenario(ledger, game, now)


async def audit_rows(scenario: Scenario) -> list[dict]:
    async with scenario.ledger._connection() as db:
        async with db.execute("SELECT * FROM audit.economy_events ORDER BY id") as cursor:
            return [dict(row) for row in await cursor.fetchall()]


async def test_quarry_shortage_charges_captured_price_and_persists_effects_once(tmp_path):
    scenario = await make_scenario(tmp_path, quarry=True)
    initial = await scenario.game.snapshot(1, 1)
    assert [(b["bid"], b["level"], b["xy"]) for b in initial["buildings"]] == [(3, 1, 51)]
    result = await scenario.game.upgrade(1, 1, 3, 5, 1, 0, immediate=True)
    assert result["goldCost"] == 6
    assert result["building"]["level"] == 2
    resource = result["cityResource"]
    assert (resource["wood"], resource["food"], resource["iron"], resource["rock"]) == (0, 9640, 9200, 9700)
    assert resource["gold"] == 1234
    # Captured instant responses precede the capacity/production field refresh.
    assert (resource["peopleWorking"], resource["rockAdd"], resource["rockMax"]) == (25, 100, 50000)
    state = await scenario.game.snapshot(1, 1)
    assert state["money"] == 94
    assert (state["resources"]["peopleWorking"], state["resources"]["rockAdd"],
            state["resources"]["rockMax"]) == (65, 366, 70000)
    assert state["resources"]["people"] == 1000
    for name in ("stateStarttime", "stateEndtime", "originalEndtime"):
        assert result["building"][name] == initial["buildings"][0][name]

    # Persisted completion remains stable across reopen, repeated checks and seed.
    reopened = ResourceEngine(scenario.ledger.database, scenario.ledger.audit_database)
    await reopened.initialize()
    recovered = GameplayEngine(reopened, scenario_id=QUARRY_SCENARIO, clock=lambda: scenario.now[0])
    await recovered.seed_city(1, 1, premium_money=999)
    await asyncio.gather(*(recovered.settle_due() for _ in range(3)))
    assert await recovered.snapshot(1, 1) == state
    for _ in range(2):
        checked = await recovered.check_queue(1, 1, result["building"]["id"], 3)
        assert checked["needTry"] is False
    assert await recovered.snapshot(1, 1) == state
    info = await recovered.building_info(1, 1, result["building"]["id"])
    assert info["currBuilding"]["level"] == 2
    assert info["nextBuilding"]["level"] == 3
    with pytest.raises(EconomyError) as error:
        await recovered.upgrade(1, 1, 3, 5, 1, 0, immediate=True)
    assert error.value.ret == 900105
    assert await recovered.snapshot(1, 1) == state


@pytest.mark.parametrize("case,expected_code", [
    ("nearby_shortage", 900105), ("extra_shortage", 900105),
    ("full_resources", 900105), ("timed", 900105),
    ("premium_shortfall", 900001), ("wrong_owner", 900102),
])
async def test_quarry_unsupported_or_unfunded_requests_leave_state_and_audit_unchanged(tmp_path, case, expected_code):
    wood = {"nearby_shortage": 177, "full_resources": 1000}.get(case, 178)
    scenario = await make_scenario(tmp_path, quarry=True, wood=wood,
                                   premium_money=5 if case == "premium_shortfall" else 100)
    if case == "extra_shortage":
        await scenario.ledger.validate_and_deduct_resources(1, 1, {"iron": 9500})
    before = await scenario.game.snapshot(1, 1)
    events = await audit_rows(scenario)
    with pytest.raises(EconomyError) as error:
        await scenario.game.upgrade(2 if case == "wrong_owner" else 1, 1, 3, 5, 1, 0,
                                    immediate=case != "timed")
    assert error.value.ret == expected_code
    assert await scenario.game.snapshot(1, 1) == before
    assert await audit_rows(scenario) == events


async def test_quarry_concurrent_upgrade_charges_once(tmp_path):
    scenario = await make_scenario(tmp_path, quarry=True)
    results = await asyncio.gather(*(scenario.game.upgrade(1, 1, 3, 5, 1, 0, immediate=True)
                                     for _ in range(5)), return_exceptions=True)
    assert sum(isinstance(result, dict) for result in results) == 1
    assert all(isinstance(result, dict) or isinstance(result, EconomyError) and result.ret == 900105
               for result in results)
    state = await scenario.game.snapshot(1, 1)
    assert state["money"] == 94
    assert state["resources"]["food"] == 9640
    assert state["resources"]["rockMax"] == 70000


async def test_quarry_late_audit_failure_rolls_back_premium_resources_building_and_effects(tmp_path):
    scenario = await make_scenario(tmp_path, quarry=True)
    async with scenario.ledger._connection() as db:
        await db.execute("CREATE TRIGGER audit.reject_upgrade BEFORE INSERT ON economy_events "
                         "WHEN NEW.action='building_upgrade' BEGIN SELECT RAISE(ABORT,'test fixture'); END")
    before = await scenario.game.snapshot(1, 1)
    events = await audit_rows(scenario)
    with pytest.raises(aiosqlite.IntegrityError):
        await scenario.game.upgrade(1, 1, 3, 5, 1, 0, immediate=True)
    assert await scenario.game.snapshot(1, 1) == before
    assert await audit_rows(scenario) == events


@pytest.mark.parametrize("immediate", [False, True])
async def test_house_effects_only_on_completion_and_do_not_repeat(tmp_path, immediate):
    scenario = await make_scenario(tmp_path)
    # Nonzero historical timestamps reveal accidental replacement in instant mode.
    async with scenario.ledger._connection() as db:
        await db.execute("UPDATE buildings SET state_start_s=1700000000,state_end_s=1700000000,"
                         "original_end_s=1700000060 WHERE bid=5")
    before = await scenario.game.snapshot(1, 1)
    result = await scenario.game.upgrade(1, 1, 5, 0, 2, 1, immediate=immediate)
    assert result["cityResource"]["peopleMax"] == 1000
    assert result["cityResource"]["peopleStable"] == 900
    if immediate:
        old = next(b for b in before["buildings"] if b["bid"] == 5)
        for name in ("stateStarttime", "stateEndtime", "originalEndtime"):
            assert result["building"][name] == old[name]
    else:
        scenario.now[0] += 59
        assert (await scenario.game.snapshot(1, 1))["resources"]["peopleMax"] == 1000
        scenario.now[0] += 1
    await asyncio.gather(*(scenario.game.settle_due() for _ in range(3)))
    completed = await scenario.game.snapshot(1, 1)
    assert (completed["resources"]["peopleMax"], completed["resources"]["peopleStable"]) == (1200, 1080)
    for _ in range(3):
        assert not (await scenario.game.check_queue(1, 1, result["building"]["id"], 3))["needTry"]
    assert await scenario.game.snapshot(1, 1) == completed
    info = await scenario.game.building_info(1, 1, result["building"]["id"])
    assert info["nextBuilding"]["level"] == 3


async def test_house_completion_failure_rolls_back_effects_with_building_state(tmp_path):
    scenario = await make_scenario(tmp_path)
    await scenario.game.upgrade(1, 1, 5, 0, 2, 1)
    events = await audit_rows(scenario)
    scenario.now[0] += 60
    async with scenario.ledger._connection() as db:
        await db.execute("CREATE TRIGGER audit.reject_complete BEFORE INSERT ON economy_events "
                         "WHEN NEW.action='building_complete' BEGIN SELECT RAISE(ABORT,'test fixture'); END")
    with pytest.raises(aiosqlite.IntegrityError):
        await scenario.game.settle_due()
    # Read raw persistence: a gameplay read would retry the failed completion.
    resources = await scenario.ledger.get_resources(1, 1)
    assert (resources.peopleMax, resources.peopleStable) == (1000, 900)
    assert await audit_rows(scenario) == events
    async with scenario.ledger._connection() as db:
        async with db.execute("SELECT level,state FROM buildings WHERE bid=5") as cursor:
            assert tuple(await cursor.fetchone()) == (1, 1)
        await db.execute("DROP TRIGGER audit.reject_complete")
    await scenario.game.settle_due()
    resources = await scenario.ledger.get_resources(1, 1)
    assert (resources.peopleMax, resources.peopleStable) == (1200, 1080)


async def test_normal_house_shortfall_uses_observed_error_without_partial_deductions(tmp_path):
    scenario = await make_scenario(tmp_path, wood=999)
    before = await scenario.game.snapshot(1, 1)
    events = await audit_rows(scenario)
    with pytest.raises(EconomyError) as error:
        await scenario.game.upgrade(1, 1, 5, 0, 2, 1)
    assert error.value.ret == 10004
    assert await scenario.game.snapshot(1, 1) == before
    assert await audit_rows(scenario) == events


async def test_timed_house_rejects_unrepresentable_completion_before_charging(tmp_path):
    scenario = await make_scenario(tmp_path)
    async with scenario.ledger._connection() as db:
        await db.execute("UPDATE cities SET peopleMax=? WHERE cid=1", (2**31 - 1 - 100,))
    before = await scenario.game.snapshot(1, 1)
    events = await audit_rows(scenario)
    with pytest.raises(EconomyError) as error:
        await scenario.game.upgrade(1, 1, 5, 0, 2, 1)
    assert error.value.ret == 900103
    assert await scenario.game.snapshot(1, 1) == before
    assert (await scenario.game.all_queues(1, 1))["buildingQueue"] == []
    assert await audit_rows(scenario) == events


async def test_pending_draft_finish_overflow_rejected_without_blocking_predecessor(tmp_path):
    scenario = await make_scenario(tmp_path)
    scenario.now[0] = 2**31 - 1 - 30
    first = (await scenario.game.start_draft(1, 1, 2, 1, 4, 4))["queues"][0]
    assert first["stateEndTime"] == 2**31 - 1 - 9
    before = await scenario.game.snapshot(1, 1)
    events = await audit_rows(scenario)
    with pytest.raises(EconomyError) as error:
        await scenario.game.start_draft(1, 1, 2, 1, 4, 4)
    assert error.value.ret == 900103
    assert await scenario.game.snapshot(1, 1) == before
    assert await audit_rows(scenario) == events
    assert len((await scenario.game.all_queues(1, 1))["soldierQueue"]) == 1
    scenario.now[0] = first["stateEndTime"]
    await scenario.game.settle_due()
    assert not (await scenario.game.check_queue(1, 1, first["qid"], 1))["needTry"]
    final = await scenario.game.snapshot(1, 1)
    assert next(t["count"] for t in final["troops"] if t["sid"] == 2) == 1
    assert (await scenario.game.all_queues(1, 1))["soldierQueue"] == []


async def test_immediate_house_at_max_clock_does_not_require_future_timer(tmp_path):
    scenario = await make_scenario(tmp_path)
    scenario.now[0] = 2**31 - 1
    result = await scenario.game.upgrade(1, 1, 5, 0, 2, 1, immediate=True)
    assert result["goldCost"] == 1
    assert result["building"]["level"] == 2 and result["building"]["state"] == 0
    assert result["building"]["stateStarttime"] == result["building"]["stateEndtime"] == 0
    final = await scenario.game.snapshot(1, 1)
    assert final["money"] == 99
    assert final["resources"]["peopleMax"] == 1200


@pytest.mark.parametrize("reserved", [False, True])
async def test_projected_troop_limit_includes_outstanding_reservations(tmp_path, reserved):
    scenario = await make_scenario(tmp_path)
    async with scenario.ledger._connection() as db:
        await db.execute("UPDATE troops SET count=? WHERE cid=1 AND sid=2",
                         (2**31 - 1 - int(reserved),))
    first = None
    if reserved:
        first = (await scenario.game.start_draft(1, 1, 2, 1, 4, 4))["queues"][0]
    before = await scenario.game.snapshot(1, 1)
    events = await audit_rows(scenario)
    with pytest.raises(EconomyError) as error:
        await scenario.game.start_draft(1, 1, 2, 1, 4, 4)
    assert error.value.ret == 900103
    assert await scenario.game.snapshot(1, 1) == before
    assert await audit_rows(scenario) == events
    if first is not None:
        scenario.now[0] = first["stateEndTime"]
        await scenario.game.settle_due()
    final = await scenario.game.snapshot(1, 1)
    assert next(t["count"] for t in final["troops"] if t["sid"] == 2) == 2**31 - 1
    assert (await scenario.game.all_queues(1, 1))["soldierQueue"] == []
