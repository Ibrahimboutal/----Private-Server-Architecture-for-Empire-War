"""Capture-derived gameplay, exact boundary clocks, and transaction failure tests."""

import asyncio
from dataclasses import dataclass

import aiosqlite
import pytest

from empire_war_private_server.economy.gameplay_engine import GameplayEngine, PROFILE_ID
from empire_war_private_server.economy.resource_engine import CityResource, EconomyError, ResourceEngine


@dataclass
class Lab:
    ledger: ResourceEngine
    game: GameplayEngine
    now: list[int]


@pytest.fixture
async def lab(tmp_path):
    ledger = ResourceEngine(tmp_path / "state.db", tmp_path / "audit.db")
    await ledger.initialize()
    await ledger.seed_synthetic_user("lab", "password", CityResource(
        food=50000, wood=50000, iron=50000, rock=50000, gold=12345, people=1000))
    now = [1_800_000_000]
    game = GameplayEngine(ledger, clock=lambda: now[0])
    await game.seed_city(1, 1)
    return Lab(ledger, game, now)


async def rows(lab, table):
    async with lab.ledger._connection() as db:
        async with db.execute(f"SELECT * FROM {table}") as cur:
            return [dict(row) for row in await cur.fetchall()]


async def test_seed_idempotent_and_synthetic_currency_is_separate(lab):
    before = await lab.game.snapshot(1, 1)
    await lab.game.seed_city(1, 1, premium_money=99999)
    assert await lab.game.snapshot(1, 1) == before
    assert before["money"] == 100
    assert before["resources"]["gold"] == 12345
    assert [(b["bid"], b["level"], b["xy"]) for b in before["buildings"]] == [
        (1, 1, 10), (2, 1, 21), (4, 1, 22), (3, 1, 51),
        (6, 1, 120), (10, 1, 132), (11, 1, 142), (8, 1, 105),
        (13, 1, 111), (15, 1, 154), (7, 1, 160), (12, 1, 161),
        (14, 1, 162), (16, 1, 163), (17, 1, 164), (18, 1, 165),
        (19, 1, 166), (20, 1, 167), (5, 1, 102), (9, 1, 144),
    ]
    assert {troop["sid"] for troop in before["troops"]} == {1, 2}
    assert all(troop["id"] > 0 and troop["cid"] == 1 and troop["count"] == 0 for troop in before["troops"])


async def test_all_catalog_buildings_are_seeded_and_inspectable(lab):
    snapshot = await lab.game.snapshot(1, 1)
    assert {building["bid"] for building in snapshot["buildings"]} == set(range(1, 21))
    by_bid = {building["bid"]: building for building in snapshot["buildings"]}
    for bid, name in [(1, "Farm"), (2, "Sawmill"), (4, "Mine"), (6, "Senate"), (20, "City Wall")]:
        info = await lab.game.building_info(1, 1, by_bid[bid]["id"])
        assert info["building"]["bid"] == bid
        assert info["currBuilding"]["name"] == name
        assert info["nextBuilding"]["name"] == name
    with pytest.raises(EconomyError) as error:
        await lab.game.upgrade(1, 1, 20, 6, 7, 1)
    assert error.value.ret == 900105


async def test_existing_city_is_expanded_without_resetting_progress(lab):
    async with lab.ledger._connection() as db:
        await db.execute("DELETE FROM buildings WHERE cid=? AND bid NOT IN (5, 9)", (1,))
        await db.execute("UPDATE buildings SET level=2 WHERE cid=? AND bid=5", (1,))
    await lab.game.seed_city(1, 1, premium_money=99999)
    snapshot = await lab.game.snapshot(1, 1)
    assert {building["bid"] for building in snapshot["buildings"]} == set(range(1, 21))
    assert next(building["level"] for building in snapshot["buildings"] if building["bid"] == 5) == 2
    assert snapshot["money"] == 100


async def test_draft_costs_and_queue_duration_match_captured_50_auxiliary(lab):
    result = await lab.game.start_draft(1, 1, 2, 50, 4, 4)
    assert set(result) == {"armys", "chiefHero", "currBuilding", "nextBuilding", "queues"}
    queue = result["queues"][0]
    assert queue["originalTimeLeft"] == queue["timeLeft"] == 1086
    assert queue["state"] == 1
    assert queue["stateEndTime"] - queue["stateStartTime"] == 1086
    resources = await lab.ledger.get_resources(1, 1)
    assert (resources.food, resources.wood, resources.iron, resources.people) == (46000, 45000, 47500, 950)
    assert resources.gold == 12345
    assert all(t["count"] == 0 for t in (await lab.game.snapshot(1, 1))["troops"])
    assert len(await rows(lab, "draft_queues")) == 1


async def test_dissolve_soldier_decrements_owned_troops_atomically(lab):
    async with lab.ledger._connection() as db:
        await db.execute("UPDATE troops SET count=100 WHERE cid=? AND sid=?", (1, 2))
    result = await lab.game.dissolve_soldier(1, 1, 2, 40, 4, 4)
    assert result["armys"][1]["count"] == 60
    assert next(t["count"] for t in (await lab.game.snapshot(1, 1))["troops"] if t["sid"] == 2) == 60
    with pytest.raises(EconomyError) as error:
        await lab.game.dissolve_soldier(1, 1, 2, 61, 4, 4)
    assert error.value.ret == 900103


async def test_pending_queue_fifo_restart_catchup_and_exactly_once(lab):
    first = await lab.game.start_draft(1, 1, 2, 50, 4, 4)
    second = await lab.game.start_draft(1, 1, 2, 50, 4, 4)
    q1, q2 = second["queues"]
    assert q2["state"] == 0 and q2["stateStartTime"] == q2["stateEndTime"] == 0
    assert q2["originalEndtime"] == 1086
    lab.now[0] = q1["stateEndTime"] - 1
    assert (await lab.game.check_queue(1, 1, q1["qid"], 1))["needTry"] is True
    lab.now[0] += 1
    assert (await lab.game.check_queue(1, 1, q1["qid"], 1))["needTry"] is False
    pending = (await lab.game.all_queues(1, 1))["soldierQueue"]
    assert len(pending) == 1 and pending[0]["qid"] == q2["qid"]
    assert pending[0]["stateStartTime"] == q1["stateEndTime"]
    # Simulate a restart with a new resource engine and clock advanced far past due.
    lab.now[0] += 99999
    reopened = ResourceEngine(lab.ledger.database, lab.ledger.audit_database)
    await reopened.initialize()
    recovered = GameplayEngine(reopened, clock=lambda: lab.now[0])
    await recovered.seed_city(1, 1)
    await asyncio.gather(*(recovered.settle_due() for _ in range(5)))
    assert (await recovered.all_queues(1, 1))["soldierQueue"] == []
    snapshot = await recovered.snapshot(1, 1)
    assert next(t["count"] for t in snapshot["troops"] if t["sid"] == 2) == 100
    for _ in range(2):
        assert not (await recovered.check_queue(1, 1, q2["qid"], 1))["needTry"]
    events = await rows(lab, "audit.economy_events")
    assert sum(row["action"] == "draft_complete" for row in events) == 2


async def test_queue_capacity_is_atomic_under_concurrent_requests(lab):
    outcomes = await asyncio.gather(*[lab.game.start_draft(1, 1, 1, 1, 4, 4) for _ in range(10)], return_exceptions=True)
    assert sum(not isinstance(outcome, Exception) for outcome in outcomes) == 2
    assert all(not isinstance(o, Exception) or isinstance(o, EconomyError) and o.ret == 900106 for o in outcomes)
    assert len(await rows(lab, "draft_queues")) == 2
    assert (await lab.ledger.get_resources(1, 1)).wood == 49700


@pytest.mark.parametrize("sid,count,x,y", [(99, 1, 4, 4), (True, 1, 4, 4), (2, 0, 4, 4),
    (2, -1, 4, 4), (2, 51, 4, 4), (2, 1.0, 4, 4), (2, 1, 0, 2), (2, 1, 10, 4)])
async def test_unsupported_or_invalid_drafts_never_charge(lab, sid, count, x, y):
    before = await lab.game.snapshot(1, 1)
    with pytest.raises(EconomyError):
        await lab.game.start_draft(1, 1, sid, count, x, y)
    assert await lab.game.snapshot(1, 1) == before
    assert await rows(lab, "draft_queues") == []


async def test_insufficient_resources_deduct_nothing(lab):
    await lab.ledger.validate_and_deduct_resources(1, 1, {"iron": 49999})
    before = await lab.game.snapshot(1, 1)
    with pytest.raises(EconomyError) as error:
        await lab.game.start_draft(1, 1, 2, 1, 4, 4)
    assert error.value.ret == 13003
    assert await lab.game.snapshot(1, 1) == before


async def test_queue_insert_failure_rolls_back_resources_and_audit(lab):
    async with lab.ledger._connection() as db:
        await db.execute("CREATE TRIGGER fail_queue BEFORE INSERT ON draft_queues BEGIN SELECT RAISE(ABORT,'fixture'); END")
    before = await lab.game.snapshot(1, 1)
    audit_count = len(await rows(lab, "audit.economy_events"))
    with pytest.raises(aiosqlite.IntegrityError):
        await lab.game.start_draft(1, 1, 2, 1, 4, 4)
    assert await lab.game.snapshot(1, 1) == before
    assert len(await rows(lab, "audit.economy_events")) == audit_count


async def test_completion_audit_failure_rolls_back_troops_and_queue_state(lab):
    queue = (await lab.game.start_draft(1, 1, 2, 1, 4, 4))["queues"][0]
    lab.now[0] = queue["stateEndTime"]
    async with lab.ledger._connection() as db:
        await db.execute("CREATE TRIGGER audit.fail_complete BEFORE INSERT ON economy_events WHEN NEW.action='draft_complete' BEGIN SELECT RAISE(ABORT,'fixture'); END")
    with pytest.raises(aiosqlite.IntegrityError):
        await lab.game.settle_due()
    assert all(row["count"] == 0 for row in await rows(lab, "troops"))
    assert (await rows(lab, "draft_queues"))[0]["state"] == 1
    async with lab.ledger._connection() as db:
        await db.execute("DROP TRIGGER audit.fail_complete")
    await lab.game.settle_due()
    assert next(t["count"] for t in await rows(lab, "troops") if t["sid"] == 2) == 1


async def test_timed_house_upgrade_matches_captured_transition(lab):
    result = await lab.game.upgrade(1, 1, 5, 0, 2, 1)
    building = result["building"]
    assert building["bid"] == 5 and building["level"] == 1 and building["state"] == 1
    assert building["stateEndtime"] - building["stateStarttime"] == 60
    assert result["cityResource"]["food"] == 49800
    assert result["cityResource"]["wood"] == 49000
    assert result["cityResource"]["iron"] == 49900
    assert result["cityResource"]["rock"] == 49800
    queued = (await lab.game.all_queues(1, 1))["buildingQueue"]
    assert queued == [building]
    lab.now[0] += 59
    assert (await lab.game.check_queue(1, 1, building["id"], 3))["needTry"] is True
    lab.now[0] += 1
    completed = await lab.game.check_queue(1, 1, building["id"], 3)
    assert not completed["needTry"]
    assert completed["building"]["level"] == 2 and completed["building"]["state"] == 0
    assert completed["building"]["originalEndtime"] == building["originalEndtime"]
    assert completed["building"]["stateEndtime"] == building["stateStarttime"]
    assert (await lab.game.snapshot(1, 1))["money"] == 100
    with pytest.raises(EconomyError) as error:
        await lab.game.upgrade(1, 1, 5, 0, 2, 1)
    assert error.value.ret == 900105


async def test_captured_farm_upgrade_uses_layout_cost_and_duration(lab):
    result = await lab.game.upgrade(1, 1, 1, 1, 0, 0)
    building = result["building"]
    assert building["bid"] == 1 and building["level"] == 1 and building["state"] == 1
    assert building["stateEndtime"] - building["stateStarttime"] == 55
    resources = await lab.ledger.get_resources(1, 1)
    assert (resources.food, resources.wood, resources.iron, resources.rock) == (49900, 49400, 49700, 49600)
    lab.now[0] += 55
    completed = await lab.game.check_queue(1, 1, building["id"], 3)
    assert completed["building"]["level"] == 2
    assert completed["building"]["state"] == 0


async def test_captured_senate_upgrade_does_not_debit_occupancy(lab):
    result = await lab.game.upgrade(1, 1, 6, 2, 0, 1)
    assert result["building"]["bid"] == 6
    assert result["building"]["state"] == 1
    resources = await lab.ledger.get_resources(1, 1)
    assert (resources.food, resources.wood, resources.iron, resources.rock, resources.people) == (
        49600, 44000, 49800, 45000, 1000)


async def test_upgrade_recovers_unique_building_after_stale_grid_coordinates(lab):
    result = await lab.game.upgrade(1, 1, 1, 9, 9, 0)
    assert result["building"]["bid"] == 1
    assert result["building"]["state"] == 1


async def test_immediate_upgrade_debits_premium_not_city_gold(lab):
    result = await lab.game.upgrade(1, 1, 5, 0, 2, 1, immediate=True)
    assert result["goldCost"] == 1
    assert result["building"]["level"] == 2 and result["building"]["state"] == 0
    state = await lab.game.snapshot(1, 1)
    assert state["money"] == 99 and state["resources"]["gold"] == 12345
    assert state["resources"]["wood"] == 49000


async def test_concurrent_upgrade_can_charge_only_once(lab):
    outcomes = await asyncio.gather(*[lab.game.upgrade(1, 1, 5, 0, 2, 1) for _ in range(5)], return_exceptions=True)
    assert sum(not isinstance(o, Exception) for o in outcomes) == 1
    assert all(not isinstance(o, Exception) or isinstance(o, EconomyError) and o.ret == 900106 for o in outcomes)
    assert (await lab.ledger.get_resources(1, 1)).wood == 49000


@pytest.mark.parametrize("failure", ["missing_resources", "missing_money", "wrong_type_id", "audit_failure"])
async def test_failed_immediate_upgrade_changes_nothing(lab, failure):
    if failure == "missing_resources":
        await lab.ledger.validate_and_deduct_resources(1, 1, {"wood": 50000})
    if failure == "missing_money":
        async with lab.ledger._connection() as db:
            await db.execute("UPDATE users SET money=0 WHERE id=1")
    if failure == "audit_failure":
        async with lab.ledger._connection() as db:
            await db.execute("CREATE TRIGGER audit.fail_upgrade BEFORE INSERT ON economy_events WHEN NEW.action='building_upgrade' BEGIN SELECT RAISE(ABORT,'fixture'); END")
    before = await lab.game.snapshot(1, 1)
    with pytest.raises((EconomyError, aiosqlite.IntegrityError)):
        await lab.game.upgrade(1, 1, 1 if failure == "wrong_type_id" else 5, 0, 2, 1, immediate=True)
    assert await lab.game.snapshot(1, 1) == before


async def test_ownership_on_all_reads_and_mutations(lab):
    for operation in [lambda: lab.game.snapshot(2, 1),
                      lambda: lab.game.start_draft(2, 1, 2, 1, 4, 4),
                      lambda: lab.game.upgrade(2, 1, 5, 0, 2, 1),
                      lambda: lab.game.building_info(2, 1, 1),
                      lambda: lab.game.check_queue(2, 1, 1, 3),
                      lambda: lab.game.all_queues(2, 1)]:
        with pytest.raises(EconomyError) as error:
            await operation()
        assert error.value.ret == 900102


async def test_get_building_state_uses_distinct_capture_shape(lab):
    await lab.game.start_draft(1, 1, 2, 1, 4, 4)
    await lab.game.start_draft(1, 1, 2, 1, 4, 4)
    state = await lab.game.building_state(1, 1)
    assert len(state["soldierQueue"]) == 1
    queue = state["soldierQueue"][0]
    assert queue["queueCount"] == queue["queueLimit"] == 2
    assert queue["draft_interval"] == 21.73913061618805
    assert "state_starttime" in queue and "stateStartTime" not in queue
