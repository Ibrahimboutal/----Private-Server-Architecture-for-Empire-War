"""Local market atomicity, captured listing scenario and bounded fault policy."""

import asyncio
from dataclasses import dataclass
import json

import aiosqlite
import pytest

from empire_war_private_server.economy.market_engine import MarketEngine, SimulatedMarketDisconnect
from empire_war_private_server.economy.resource_engine import CityResource, EconomyError, ResourceEngine


REQUEST = {"type": "Market", "action": "sellToUser", "cityId": 1,
           "resType": 0, "resCount": 1, "gold": 0.119999997317791,
           "hour": 12, "unionSel": 0}
FRAME = {"packet_type": 4, "wire_length": 123, "plaintext_length": 100}


@dataclass
class MarketLab:
    ledger: ResourceEngine
    market: MarketEngine


@pytest.fixture
async def lab(tmp_path):
    ledger = ResourceEngine(tmp_path / "state.db", tmp_path / "audit.db")
    await ledger.initialize()
    await ledger.seed_synthetic_user("market-test-fixture", "synthetic-only", CityResource(
        food=100, wood=200, iron=300, rock=400, gold=500, people=50,
        lastupdate=1_800_000_000))
    return MarketLab(ledger, MarketEngine(ledger, clock=lambda: 1_800_000_000))


def simulated(ledger, threshold=3):
    return MarketEngine(ledger, simulate_int32_crash=True,
                        overflow_breaker_threshold=threshold, simulation_delay_ms=10,
                        clock=lambda: 1_800_000_000)


async def rows(ledger, table):
    async with ledger._connection() as db:
        async with db.execute(f"SELECT * FROM {table} ORDER BY rowid") as cursor:
            return [dict(row) for row in await cursor.fetchall()]


async def sell(market, **changes):
    return await market.sell_to_user(1, 1, {**REQUEST, **changes}, frame_metadata=FRAME)


async def test_captured_listing_deducts_food_and_persists_owned_view(lab):
    before = await lab.ledger.get_resources(1, 1)
    events_before = await rows(lab.ledger, "audit.economy_events")
    response = await sell(lab.market)
    assert (response["type"], response["action"], response["ret"]) == ("Market", "sellToUser", 0)
    after = await lab.ledger.get_resources(1, 1)
    assert after.food == before.food - 1
    for name, value in before.to_wire().items():
        if name not in ("food", "lastupdate"):
            assert after.to_wire()[name] == value
    listings = await rows(lab.ledger, "market_listings")
    assert len(listings) == 1
    assert listings[0]["user_id"] == listings[0]["cid"] == 1
    assert (listings[0]["res_type"], listings[0]["res_count"]) == (0, 1)
    own = await lab.market.own_trades(1, 1)
    expected_own = json.loads(listings[0]["trade_json"])
    expected_own.pop("tooLong")
    expected_own.update(originalEndtime=0, starttime=0)
    assert own == {"type": "Market", "action": "doGetCityTrade", "ret": 0,
                   "tradingList": [expected_own]}
    assert len(await rows(lab.ledger, "audit.economy_events")) == len(events_before) + 2
    audit = await rows(lab.ledger, "audit.market_events")
    assert len(audit) == 1 and audit[0]["outcome"] == "listed" and audit[0]["ret"] == 0
    assert json.loads(audit[0]["frame_json"]) == FRAME
    assert json.loads(audit[0]["packet_json"]) == REQUEST


async def test_repeated_request_is_a_second_paid_listing(lab):
    for _ in range(2):
        assert (await sell(lab.market, requestId="same-local-request"))["ret"] == 0
    assert (await lab.ledger.get_resources(1, 1)).food == 98
    listings = await rows(lab.ledger, "market_listings")
    assert len(listings) == 2 and listings[0]["id"] != listings[1]["id"]
    assert len((await lab.market.own_trades(1, 1))["tradingList"]) == 2


async def test_equivalent_float32_price_uses_captured_listing_quote(lab):
    assert (await sell(lab.market, gold=0.12))["ret"] == 0
    listing = (await rows(lab.ledger, "market_listings"))[0]
    assert listing["gold"] == REQUEST["gold"]
    assert (await lab.ledger.get_resources(1, 1)).food == 99


async def test_safe_overflow_is_logged_with_packet_and_frame_without_mutation(lab):
    before = await lab.ledger.get_resources(1, 1)
    economy_before = await rows(lab.ledger, "audit.economy_events")
    result = await sell(lab.market, gold=2147483646.0, experiment={"case": "boundary", "sequence": 1})
    assert result["ret"] == 19011
    assert await lab.ledger.get_resources(1, 1) == before
    assert await rows(lab.ledger, "market_listings") == []
    assert await rows(lab.ledger, "audit.economy_events") == economy_before
    event = (await rows(lab.ledger, "audit.market_events"))[0]
    assert event["outcome"] == "overflow" and event["ret"] == 19011 and event["simulated"] == 0
    assert json.loads(event["packet_json"]) == {
        **REQUEST, "gold": 2147483646.0, "experiment": {"case": "boundary", "sequence": 1}}
    assert json.loads(event["frame_json"]) == FRAME
    assert (await lab.market.research_status())["simulation_enabled"] is False


@pytest.mark.parametrize("changes,ret", [
    ({"gold": -1}, 900103), ({"hour": -1}, 900103), ({"resCount": -1}, 900103),
    ({"gold": True}, 900103), ({"resCount": 1.0}, 900103),
    ({"gold": 0.5}, 900105),
])
async def test_invalid_or_unsupported_quote_is_audited_without_economy_changes(lab, changes, ret):
    before = await lab.ledger.get_resources(1, 1)
    economy_before = await rows(lab.ledger, "audit.economy_events")
    assert (await sell(lab.market, **changes))["ret"] == ret
    assert await lab.ledger.get_resources(1, 1) == before
    assert await rows(lab.ledger, "market_listings") == []
    assert await rows(lab.ledger, "audit.economy_events") == economy_before
    event = (await rows(lab.ledger, "audit.market_events"))[0]
    assert event["ret"] == ret
    assert event["outcome"] == ("invalid" if ret == 900103 else "unsupported")


async def test_wrong_owner_or_request_city_fails_before_market_audit(lab):
    before = await lab.ledger.get_resources(1, 1)
    for operation in (
        lambda: lab.market.sell_to_user(2, 1, REQUEST, frame_metadata=FRAME),
        lambda: lab.market.sell_to_user(1, 1, {**REQUEST, "cityId": 2}, frame_metadata=FRAME),
        lambda: lab.market.own_trades(2, 1),
    ):
        with pytest.raises(EconomyError) as error:
            await operation()
        assert error.value.ret == 900102
    assert await lab.ledger.get_resources(1, 1) == before
    assert await rows(lab.ledger, "market_listings") == []
    assert await rows(lab.ledger, "audit.market_events") == []
    state = await lab.market.research_status()
    assert state["consecutive_overflows"] == 0 and state["breaker_tripped"] is False


async def test_concurrent_requests_cannot_overspend_food(lab):
    async with lab.ledger._transaction() as db:
        await db.execute("UPDATE cities SET food=1 WHERE cid=1")
    results = await asyncio.gather(sell(lab.market), sell(lab.market))
    assert sorted(response["ret"] for response in results) == [0, 19004]
    assert (await lab.ledger.get_resources(1, 1)).food == 0
    assert len(await rows(lab.ledger, "market_listings")) == 1
    assert len(await rows(lab.ledger, "audit.market_events")) == 2


async def test_simulation_commits_audit_then_trips_and_stays_latched_on_restart(lab):
    market = simulated(lab.ledger, threshold=2)
    before = await lab.ledger.get_resources(1, 1)
    # A bounded disconnect signal is the only simulated failure; there is no
    # process termination or blocking event-loop hang in the engine contract.
    async with asyncio.timeout(10):
        for expected in (1, 2):
            with pytest.raises(SimulatedMarketDisconnect):
                await sell(market, gold=2147483646.0)
            assert len(await rows(lab.ledger, "audit.market_events")) == expected
    state = await market.research_status()
    assert state["configured_simulation"] is True and state["threshold"] == 2
    assert state["breaker_tripped"] is True and state["simulation_enabled"] is False
    reopened = ResourceEngine(lab.ledger.database, lab.ledger.audit_database)
    await reopened.initialize()
    restarted = simulated(reopened, threshold=2)
    assert (await restarted.research_status())["breaker_tripped"] is True
    assert (await sell(restarted, gold=2147483646.0))["ret"] == 19011
    events = await rows(lab.ledger, "audit.market_events")
    assert [event["simulated"] for event in events] == [1, 1, 0]
    assert await lab.ledger.get_resources(1, 1) == before
    assert await rows(lab.ledger, "market_listings") == []
    assert (await sell(restarted))["ret"] == 0
    assert (await restarted.research_status())["breaker_tripped"] is True
    assert (await restarted.research_status())["simulation_enabled"] is False


async def test_successful_sale_resets_consecutive_overflows_before_breaker_trip(lab):
    market = simulated(lab.ledger)
    with pytest.raises(SimulatedMarketDisconnect):
        await sell(market, gold=2147483646.0)
    assert (await market.research_status())["consecutive_overflows"] == 1
    assert (await sell(market))["ret"] == 0
    assert (await market.research_status())["consecutive_overflows"] == 0
    with pytest.raises(SimulatedMarketDisconnect):
        await sell(market, gold=2147483646.0)
    state = await market.research_status()
    assert state["consecutive_overflows"] == 1 and state["breaker_tripped"] is False


async def test_concurrent_overflows_cannot_exceed_simulated_failure_budget(lab):
    market = simulated(lab.ledger, threshold=2)
    results = await asyncio.gather(*(sell(market, gold=2147483646.0) for _ in range(5)), return_exceptions=True)
    assert sum(isinstance(value, SimulatedMarketDisconnect) for value in results) == 2
    safe = [value for value in results if not isinstance(value, SimulatedMarketDisconnect)]
    assert len(safe) == 3 and all(isinstance(value, dict) and value["ret"] == 19011 for value in safe)
    assert len(await rows(lab.ledger, "audit.market_events")) == 5
    assert (await market.research_status())["breaker_tripped"] is True


@pytest.mark.parametrize("overflow", [False, True])
async def test_market_audit_failure_rolls_back_resources_listing_economy_audit_and_breaker(lab, overflow):
    market = simulated(lab.ledger, threshold=1)
    before = await lab.ledger.get_resources(1, 1)
    economy_before = await rows(lab.ledger, "audit.economy_events")
    status_before = await market.research_status()
    async with lab.ledger._connection() as db:
        await db.execute("CREATE TRIGGER audit.reject_market_event BEFORE INSERT ON market_events BEGIN SELECT RAISE(ABORT,'fixture'); END")
    with pytest.raises(aiosqlite.IntegrityError):
        await sell(market, **({"gold": 2147483646.0} if overflow else {}))
    assert await lab.ledger.get_resources(1, 1) == before
    assert await rows(lab.ledger, "market_listings") == []
    assert await rows(lab.ledger, "audit.market_events") == []
    assert await rows(lab.ledger, "audit.economy_events") == economy_before
    assert await market.research_status() == status_before


async def test_overflow_audit_redacts_nested_credentials_and_preserves_research_fields(lab):
    secrets = {"password": "SECRET_PASS", "passport": "SECRET_USER",
               "sessionId": "SECRET_SESSION", "token": "SECRET_TOKEN", "authorization": "SECRET_AUTH"}
    result = await sell(lab.market, gold=2147483646.0, **secrets,
                        experiment={"case": "sanitization", "nested": [dict(secrets), {"sequence": 7}]})
    assert result["ret"] == 19011
    raw = (await rows(lab.ledger, "audit.market_events"))[0]["packet_json"]
    assert all(secret not in raw for secret in secrets.values())
    packet = json.loads(raw)
    assert packet["experiment"]["case"] == "sanitization"
    assert packet["experiment"]["nested"][1] == {"sequence": 7}
    assert packet["gold"] == 2147483646.0 and packet["resCount"] == 1


async def test_listing_insert_failure_rolls_back_charge_and_audits(lab):
    before = await lab.ledger.get_resources(1, 1)
    audit_before = await rows(lab.ledger, "audit.economy_events")
    async with lab.ledger._connection() as db:
        await db.execute("CREATE TRIGGER reject_listing BEFORE INSERT ON market_listings BEGIN SELECT RAISE(ABORT,'fixture'); END")
    with pytest.raises(aiosqlite.IntegrityError):
        await sell(lab.market)
    assert await lab.ledger.get_resources(1, 1) == before
    assert await rows(lab.ledger, "audit.economy_events") == audit_before
    assert await rows(lab.ledger, "audit.market_events") == []


async def test_native_listing_id_overflow_rolls_back_charge(lab):
    await sell(lab.market)
    before = await lab.ledger.get_resources(1, 1)
    audit_before = await rows(lab.ledger, "audit.economy_events")
    async with lab.ledger._connection() as db:
        await db.execute("UPDATE sqlite_sequence SET seq=? WHERE name='market_listings'", (2**31 - 1,))
    with pytest.raises(EconomyError) as error:
        await sell(lab.market)
    assert error.value.ret == 900103
    assert await lab.ledger.get_resources(1, 1) == before
    assert await rows(lab.ledger, "audit.economy_events") == audit_before
    assert len(await rows(lab.ledger, "market_listings")) == 1
    assert len(await rows(lab.ledger, "audit.market_events")) == 1


async def test_fixture_expiry_is_explicit_and_never_refunds_or_settles(lab):
    await sell(lab.market)
    before = await lab.ledger.get_resources(1, 1)
    events = await rows(lab.ledger, "audit.economy_events")
    lab.market.clock = lambda: 1_800_000_000 + 43200
    assert (await lab.market.own_trades(1, 1))["ret"] == 900105
    assert await lab.ledger.get_resources(1, 1) == before
    assert await rows(lab.ledger, "audit.economy_events") == events
    assert await lab.market.listing_count(1, 1) == 1


@pytest.mark.parametrize("gold", [float("nan"), float("inf"), 10**10000], ids=["nan", "infinity", "huge-integer"])
async def test_direct_nonfinite_and_huge_integer_research_inputs_are_auditable(lab, gold):
    assert (await sell(lab.market, gold=gold))["ret"] == 19011
    packet = json.loads((await rows(lab.ledger, "audit.market_events"))[0]["packet_json"])
    assert isinstance(packet["gold"], dict)  # Tagged research value, valid JSON.
    assert await rows(lab.ledger, "market_listings") == []
