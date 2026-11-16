"""Transactional Phase 2 gameplay for explicitly captured synthetic scenarios.

Queue timing is fixed by captured quotes, not inferred bonus formulas. Every
public operation validates city ownership, advances persisted due work, and
returns one consistent transaction snapshot. Lazy advancement plus a server
worker makes completion restart-safe without trusting client completion claims.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from importlib.resources import files
import json
import math
import time
from typing import Any

import aiosqlite

from .resource_engine import BALANCES, CityResource, EconomyError, ResourceEngine
from .time_util import float32, signed, time_to_finish_now_gold

PROFILE_ID = "captured-phase2-v1"
QUARRY_PROFILE_ID = "captured-quarry-shortage-v1"
UNSUPPORTED = 900105
BUSY = 900106


def integer(value: object, name: str, lower: int = 1, upper: int = 2**31 - 1) -> int:
    """Reject bool, float and unbounded integers at the action boundary."""
    if type(value) is not int or not lower <= value <= upper:
        raise EconomyError(900103, f"Invalid {name}")
    return value


class GameplayEngine:
    """Captured training/House or isolated Quarry shortage on a synthetic city.

    Pending queues run sequentially per barracks. Catch-up starts the next queue
    at its predecessor's end, rather than adding downtime after a restart.
    This deterministic scheduler is local policy; wire states/times follow the
    captures. Completed queue rows remain for idempotent checks and auditability.
    """

    def __init__(self, resources: ResourceEngine, *, scenario_id: str = PROFILE_ID,
                 clock: Callable[[], float] = time.time) -> None:
        self.resources = resources
        self.clock = clock
        if scenario_id not in (PROFILE_ID, QUARRY_PROFILE_ID):
            raise ValueError("unsupported captured scenario")
        self.profile_id = scenario_id
        self._profile = json.loads(files("empire_war_private_server").joinpath(
            "data/phase2_scenario.json").read_text(encoding="utf-8"))
        if self._profile["version"] != 1 or self._profile["profile_id"] != PROFILE_ID:
            raise ValueError("unsupported captured scenario")
        self._draft = self._profile["draft"]
        quarry = scenario_id == QUARRY_PROFILE_ID
        self._armies = {} if quarry else {item["sid"]: item for item in self._draft["army_entries"]}
        quotes = self._profile["quarry"]["quotes"] if quarry else self._profile["buildings"]
        self._buildings = {(item["bid"], item["target_level"]): item for item in quotes}
        expected_bid = 3 if quarry else 5
        if set(self._armies) != (set() if quarry else {1, 2}) or set(self._buildings) != {(expected_bid, 1), (expected_bid, 2)}:
            raise ValueError("unexpected supported scenario entries")
        self._effects = self._profile["quarry"]["effects"] if quarry else self._profile["house_effects"]
        layout = json.loads(files("empire_war_private_server").joinpath(
            "data/city_layout.json").read_text(encoding="utf-8"))
        if layout["version"] != 1:
            raise ValueError("unsupported city layout version")
        self._city_layout = [] if quarry else layout["buildings"]
        self._inspection_views = {
            (entry["bid"], entry["level"]): entry["view"] for entry in self._city_layout
        }
        expected_effects = {"peopleWorking": 40, "rockAdd": 266, "rockMax": 20000} if quarry else {"peopleMax": 200, "peopleStable": 180}
        if self._effects != expected_effects:
            raise ValueError("unexpected captured building effects")
        if (self._draft["barracks_bid"], self._draft["barracks_level"],
            self._draft["queue_limit"], self._draft["max_count"]) != (9, 1, 2, 50):
            raise ValueError("unsupported barracks scenario")
        for sid, quote in self._armies.items():
            costs = {name: quote[f"{name}Need"] for name in BALANCES}
            resources._validate_costs(costs)
            if costs != resources.troop_costs[sid]:
                raise ValueError("captured draft costs disagree with authoritative base table")
            duration = quote["draftTime"]
            if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
                raise ValueError("invalid captured draft duration")
        for quote in self._buildings.values():
            resources._validate_costs(quote["costs"])
            if quote["conditions"] or quote["costs"]["people"] != 0:
                raise ValueError("unverified building prerequisite or population effect")
            self._duration(quote)

    def _now(self) -> int:
        value = self.clock()
        if not math.isfinite(value) or not 0 <= value <= 2**31 - 1:
            raise ValueError("scenario clock must be representable Unix seconds")
        return int(value)

    @staticmethod
    def _duration(quote: dict[str, Any]) -> int:
        value = quote["duration_seconds"]
        if type(value) not in (int, float) or not math.isfinite(value) or not 1 <= value <= 2**31 - 1:
            raise ValueError("invalid captured building duration")
        # Only House supports timed upgrades. Quarry uses its captured instant
        # price, so this narrowing never invents a fractional timer rule.
        return int(value)

    @staticmethod
    async def _one(db: aiosqlite.Connection, sql: str, args: tuple = ()) -> aiosqlite.Row | None:
        async with db.execute(sql, args) as cursor:
            return await cursor.fetchone()

    @staticmethod
    async def _all(db: aiosqlite.Connection, sql: str, args: tuple = ()) -> list[aiosqlite.Row]:
        async with db.execute(sql, args) as cursor:
            return await cursor.fetchall()

    async def seed_city(self, user_id: int, city_id: int, *, premium_money: int = 100) -> None:
        """Add the approved synthetic fixtures once; never reset existing progress."""
        integer(premium_money, "premium money", 0)
        async with self.resources._transaction() as db:
            await self.resources._read(db, user_id, city_id)
            existing = await self._one(db, "SELECT scenario_id FROM scenario_cities WHERE cid=?", (city_id,))
            if existing is not None:
                if existing["scenario_id"] != self.profile_id:
                    raise EconomyError(UNSUPPORTED, "City belongs to a different research scenario")
                return
            if await self._one(db, "SELECT id FROM buildings WHERE cid=?", (city_id,)):
                raise EconomyError(UNSUPPORTED, "Refusing to replace an existing city layout")
            await db.execute("INSERT INTO scenario_cities(cid,scenario_id) VALUES (?,?)", (city_id, self.profile_id))
            # Synthetic layout: instance IDs are allocated locally, never copied.
            layout = [(3, 1, 51, 0)] if self.profile_id == QUARRY_PROFILE_ID else [(5, 1, 102, 1), (9, 1, 144, 1)]
            await db.executemany("INSERT INTO buildings(cid,bid,level,xy,inner) VALUES (?,?,?,?,?)",
                                 [(city_id, *entry) for entry in layout])
            await db.executemany("INSERT INTO troops(cid,sid,count) VALUES (?,?,0)", [(city_id, sid) for sid in self._armies])
            before = await self._one(db, "SELECT money FROM users WHERE id=?", (user_id,))
            await db.execute("UPDATE users SET money=? WHERE id=?", (premium_money, user_id))
            await self.resources._audit(db, user_id, city_id, "scenario_seed",
                {"money": before["money"]}, {"money": premium_money, "scenario": self.profile_id,
                    "buildings": layout, "troops": {str(sid): 0 for sid in self._armies}}, {})

    async def _inspection_view(self, db: aiosqlite.Connection, building: aiosqlite.Row) -> dict[str, Any] | None:
        """Use captured detail quotes with local prerequisite counts and state."""
        recorded = self._inspection_views.get((building["bid"], building["level"]))
        if recorded is None:
            return None
        view = deepcopy(recorded)
        levels = {row["bid"]: row["level"] for row in await self._all(
            db, "SELECT bid,MAX(level) AS level FROM buildings WHERE cid=? GROUP BY bid",
            (building["cid"],))}
        for label in ("currBuilding", "nextBuilding"):
            quote = view[label]
            for condition in quote["conditions"]:
                required_bid = condition.pop("required_bid")
                condition["currentOwn"] = levels.get(required_bid, 0)
                condition["canUpgrade"] = condition["currentOwn"] >= condition["upgradeNeedLevel"]
            # These additional captured inspection quotes have no enabled
            # economic transition. The server can display them consistently.
            quote["canUpgrade"] = 2  # Captured disabled enum; no mutation is supported.
        if building["bid"] == 10:
            view.update(freshLastTime=0, heroCount=0, recruitHeros=[])
        return {"building": self._building_wire(building), **view}

    async def _require_city(self, db: aiosqlite.Connection, user_id: int, city_id: int) -> None:
        integer(user_id, "userId")
        integer(city_id, "cityId")
        await self.resources._read(db, user_id, city_id)
        row = await self._one(db, "SELECT scenario_id FROM scenario_cities WHERE cid=?", (city_id,))
        if row is None or row["scenario_id"] != self.profile_id:
            raise EconomyError(UNSUPPORTED, "City is not initialized for this captured scenario")

    @staticmethod
    def _building_wire(building: aiosqlite.Row) -> dict[str, int]:
        return {"id": building["id"], "cid": building["cid"], "bid": building["bid"],
                "level": building["level"], "xy": building["xy"], "state": building["state"],
                "stateStarttime": building["state_start_s"], "stateEndtime": building["state_end_s"],
                "originalEndtime": building["original_end_s"]}

    def _queue_wire(self, row: aiosqlite.Row, now: int) -> dict[str, Any]:
        active = row["state"] == 1
        return {"qid": row["qid"], "sid": row["sid"], "sname": self._armies[row["sid"]]["sname"],
                "count": row["soldier_count"], "state": row["state"], "xy": row["xy"],
                "stateStartTime": row["start_s"] if active else 0,
                "stateEndTime": row["end_s"] if active else 0,
                "originalEndtime": row["end_s"] if active else row["duration_s"],
                "originalTimeLeft": row["duration_s"],
                "timeLeft": max(0, row["end_s"] - now) if active else row["duration_s"]}

    def _effect_snapshot(self, before: CityResource) -> dict[str, int | float]:
        """Validate future effects before accepting a paid upgrade."""
        values = before.to_wire()
        for name, delta in self._effects.items():
            values[name] += delta
        try:
            after = CityResource(**values).to_wire()
        except ValueError as exc:
            raise EconomyError(900103, "Building effect exceeds resource bounds") from exc
        for name in self._effects:
            if after[name] != values[name] or after[name] <= getattr(before, name):
                raise EconomyError(900103, "Building effect exceeds resource precision")
        return after

    async def _apply_building_effects(self, db: aiosqlite.Connection, user_id: int,
                                      city_id: int, bid: int) -> None:
        """Apply only observed level 1→2 deltas inside the upgrade transaction."""
        if (bid, 2) not in self._buildings:
            raise EconomyError(UNSUPPORTED, "No captured completion effects")
        before = await self.resources._read(db, user_id, city_id)
        after = self._effect_snapshot(before)
        # Field names are checked against fixed allowlists in __init__.
        columns = ",".join(f"{name}=?" for name in self._effects)
        await db.execute(f"UPDATE cities SET {columns} WHERE cid=? AND user_id=?",
                         (*(after[name] for name in self._effects), city_id, user_id))
        await self.resources._audit(db, user_id, city_id, "building_effects",
                                    before.to_wire(), after, self._effects)

    async def _settle(self, db: aiosqlite.Connection, user_id: int, city_id: int, now: int) -> None:
        for building in await self._all(db, "SELECT * FROM buildings WHERE cid=? AND state=1 AND state_end_s<=? ORDER BY id", (city_id, now)):
            if building["target_level"] is None:
                raise RuntimeError("upgrading building has no target level")
            await self._apply_building_effects(db, user_id, city_id, building["bid"])
            await db.execute("UPDATE buildings SET level=target_level,target_level=NULL,state=0,state_end_s=state_start_s WHERE id=?", (building["id"],))
            after = await self._one(db, "SELECT * FROM buildings WHERE id=?", (building["id"],))
            await self.resources._audit(db, user_id, city_id, "building_complete",
                                       self._building_wire(building), self._building_wire(after), {})
        barracks = await self._all(db, "SELECT id FROM buildings WHERE cid=? AND bid=9 ORDER BY id", (city_id,))
        for building in barracks:
            while True:
                queue = await self._one(db, "SELECT * FROM draft_queues WHERE building_id=? AND state=1 ORDER BY qid LIMIT 1", (building["id"],))
                if queue is None or queue["end_s"] > now:
                    break
                troop = await self._one(db, "SELECT count FROM troops WHERE cid=? AND sid=?", (city_id, queue["sid"]))
                count = signed((troop["count"] if troop else 0) + queue["soldier_count"])
                await db.execute("INSERT INTO troops(cid,sid,count) VALUES (?,?,?) ON CONFLICT(cid,sid) DO UPDATE SET count=excluded.count", (city_id, queue["sid"], count))
                await db.execute("UPDATE draft_queues SET state=2 WHERE qid=?", (queue["qid"],))
                await self.resources._audit(db, user_id, city_id, "draft_complete",
                    {"qid": queue["qid"], "sid": queue["sid"], "count": count - queue["soldier_count"]},
                    {"qid": queue["qid"], "sid": queue["sid"], "count": count, "completed_at": queue["end_s"]}, {})
                pending = await self._one(db, "SELECT * FROM draft_queues WHERE building_id=? AND state=0 ORDER BY qid LIMIT 1", (building["id"],))
                if pending is not None:
                    end = signed(queue["end_s"] + pending["duration_s"])
                    await db.execute("UPDATE draft_queues SET state=1,start_s=?,end_s=? WHERE qid=?", (queue["end_s"], end, pending["qid"]))
                    await self.resources._audit(db, user_id, city_id, "draft_activate",
                        {"qid": pending["qid"], "state": 0},
                        {"qid": pending["qid"], "state": 1, "start_s": queue["end_s"], "end_s": end}, {})

    async def settle_due(self) -> None:
        """Advance all scenario cities; safe against concurrent query/completion."""
        async with self.resources._connection() as db:
            cities = await self._all(db, "SELECT c.user_id,c.cid FROM cities c JOIN scenario_cities s ON s.cid=c.cid WHERE s.scenario_id=?", (self.profile_id,))
        for city in cities:
            async with self.resources._transaction() as db:
                await self._require_city(db, city["user_id"], city["cid"])
                await self._settle(db, city["user_id"], city["cid"], self._now())

    async def snapshot(self, user_id: int, city_id: int) -> dict[str, Any]:
        """One settled snapshot for login/sync; no captured identity or balance."""
        async with self.resources._transaction() as db:
            await self._require_city(db, user_id, city_id)
            await self._settle(db, user_id, city_id, self._now())
            resource = await self.resources._read(db, user_id, city_id)
            user = await self._one(db, "SELECT money FROM users WHERE id=?", (user_id,))
            buildings = await self._all(db, "SELECT * FROM buildings WHERE cid=? ORDER BY id", (city_id,))
            troops = await self._all(db, "SELECT id,sid,count FROM troops WHERE cid=? ORDER BY sid", (city_id,))
            return {"resources": resource.to_wire(), "money": user["money"],
                    "buildings": [self._building_wire(item) for item in buildings],
                    "troops": [{"id": item["id"], "cid": city_id, "sid": item["sid"], "count": item["count"]} for item in troops]}

    def login_configuration(self) -> dict[str, Any]:
        quarry = self.profile_id == QUARRY_PROFILE_ID
        return deepcopy({"cfgSoldier": [] if quarry else self._profile["cfg_soldier"],
                         "cfgBuilding": [b for b in self._profile["cfg_building"] if b["bid"] in ({3} if quarry else {5, 9})],
                         "cfgBarracksLimitList": [] if quarry else self._profile["cfg_barracks_limits"]})

    async def _barracks_view(self, db: aiosqlite.Connection, building: aiosqlite.Row, now: int) -> dict[str, Any]:
        if building["bid"] != 9 or building["level"] != 1:
            raise EconomyError(UNSUPPORTED, "No captured quote for this barracks level")
        queues = await self._all(db, "SELECT q.*,b.xy FROM draft_queues q JOIN buildings b ON b.id=q.building_id WHERE q.building_id=? AND q.state<>2 ORDER BY q.qid", (building["id"],))
        troop_rows = await self._all(db, "SELECT sid,count FROM troops WHERE cid=?", (building["cid"],))
        counts = {row["sid"]: row["count"] for row in troop_rows}
        wire_queues = [self._queue_wire(row, now) for row in queues]
        army_building = {"xy": building["xy"], "level": 1, "usedQueue": len(queues),
                         "queueTimeLeft": next((max(0, q["end_s"] - now) for q in queues if q["state"] == 1), 0),
                         "totalTimeLeft": sum(q["timeLeft"] for q in wire_queues)}
        entries = []
        owner = await self._one(db, "SELECT user_id FROM cities WHERE cid=?", (building["cid"],))
        resource = await self.resources._read(db, owner["user_id"], building["cid"])
        for quote in self._armies.values():
            costs = {name: quote[f"{name}Need"] for name in BALANCES}
            entry = deepcopy(quote)
            entry.update(count=counts.get(quote["sid"], 0), armyBuildings=[dict(army_building)],
                canDraft=building["state"] == 0 and len(queues) < self._draft["queue_limit"] and all(getattr(resource, key) >= cost for key, cost in costs.items()),
                conditions=[{"canUpgrade": True, "collegeBuilding": False, "currentOwn": 1,
                             "type": 0, "upgradeNeedLevel": 1, "upgradeNeedName": "Barracks"}])
            entries.append(entry)
        current = deepcopy(self._draft["current_building"])
        following = deepcopy(self._draft["next_building"])
        # canUpgrade is a captured status code, not an affordability boolean.
        return {"armys": entries, "chiefHero": deepcopy(self._draft["chief_hero_shape"]),
                "currBuilding": current, "nextBuilding": following, "queues": wire_queues}

    async def start_draft(self, user_id: int, city_id: int, sid: int, count: int, x: int, y: int) -> dict[str, Any]:
        integer(sid, "sid")
        integer(count, "soldierCount", 1, self._draft["max_count"])
        integer(x, "x", 0, 9)
        integer(y, "y", 0, 9)
        if sid not in self._armies:
            raise EconomyError(UNSUPPORTED, "Troop has no approved captured draft scenario")
        quote = self._armies[sid]
        # Native DraftArmyDialog::refreshTimeLabel uses float32 operands/product.
        duration = signed(int(float32(float32(count) * float32(quote["draftTime"]))))
        async with self.resources._transaction() as db:
            await self._require_city(db, user_id, city_id)
            now = self._now()
            await self._settle(db, user_id, city_id, now)
            building = await self._one(db, "SELECT * FROM buildings WHERE cid=? AND xy=? AND inner=1 AND bid=9", (city_id, 100 + 10*x + y))
            if building is None:
                raise EconomyError(13000, "No barracks at this location")
            if building["level"] != 1:
                raise EconomyError(UNSUPPORTED, "Barracks level has no approved draft scenario")
            existing = await self._all(db, "SELECT qid,state,end_s,duration_s FROM draft_queues WHERE building_id=? AND state<>2", (building["id"],))
            if building["state"] != 0 or len(existing) >= self._draft["queue_limit"]:
                raise EconomyError(BUSY, "Barracks queue is full or building is busy")
            scheduled_end = max([now] + [q["end_s"] for q in existing if q["state"] == 1])
            scheduled_end += sum(q["duration_s"] for q in existing if q["state"] == 0) + duration
            integer(scheduled_end, "queue completion time", 0)
            troop = await self._one(db, "SELECT count FROM troops WHERE cid=? AND sid=?", (city_id, sid))
            queued = await self._one(db, "SELECT COALESCE(SUM(soldier_count),0) AS count FROM draft_queues WHERE cid=? AND sid=? AND state<>2", (city_id, sid))
            if (troop["count"] if troop else 0) + queued["count"] + count > 2**31 - 1:
                raise EconomyError(900103, "Completed troop count would overflow")
            costs = self.resources._validate_costs({name: quote[f"{name}Need"] * count for name in BALANCES})
            result = await self.resources._deduct(db, user_id, city_id, costs, "startDraftQueue")
            active = not existing
            end = signed(now + duration) if active else 0
            async with db.execute("INSERT INTO draft_queues(cid,building_id,sid,soldier_count,duration_s,state,start_s,end_s,audit_id) VALUES (?,?,?,?,?,?,?,?,?)",
                (city_id, building["id"], sid, count, duration, int(active), now if active else 0, end, result.audit_id)) as cursor:
                qid = cursor.lastrowid
            await self.resources._audit(db, user_id, city_id, "draft_enqueue", {},
                {"qid": qid, "sid": sid, "count": count, "duration_s": duration, "building_id": building["id"], "resource_audit_id": result.audit_id}, {})
            return await self._barracks_view(db, building, now)

    async def upgrade(self, user_id: int, city_id: int, bid: int, x: int, y: int, inner: int, *, immediate: bool = False) -> dict[str, Any]:
        """Execute only the selected captured transition; ignore client prices."""
        integer(bid, "buildingId")
        integer(x, "x", 0, 9)
        integer(y, "y", 0, 9)
        integer(inner, "inner", 0, 1)
        async with self.resources._transaction() as db:
            await self._require_city(db, user_id, city_id)
            now = self._now()
            await self._settle(db, user_id, city_id, now)
            building = await self._one(db, "SELECT * FROM buildings WHERE cid=? AND xy=? AND inner=?", (city_id, 100*inner + 10*x + y, inner))
            if building is None or building["bid"] != bid:
                raise EconomyError(900103, "buildingId does not match the building at this location")
            quote = self._buildings.get((bid, building["level"] + 1))
            if quote is None or building["level"] != 1:
                raise EconomyError(UNSUPPORTED, "Building transition has no approved captured quote")
            if bid == 3 and not immediate:
                raise EconomyError(UNSUPPORTED, "Quarry fixture supports only the captured instant upgrade")
            if building["state"] != 0:
                raise EconomyError(BUSY, "Building already has an upgrade in progress")
            if await self._one(db, "SELECT id FROM buildings WHERE cid=? AND state=1", (city_id,)):
                raise EconomyError(BUSY, "The local scenario permits one building upgrade at a time")
            resource = await self.resources._read(db, user_id, city_id)
            self._effect_snapshot(resource)
            costs = quote["costs"]
            shortages = {key for key, cost in costs.items() if getattr(resource, key) < cost}
            duration = self._duration(quote)
            money_cost = time_to_finish_now_gold(duration) if immediate else 0
            if bid == 3:
                captured = self._profile["quarry"]
                if shortages != {"wood"} or costs["wood"] - int(resource.wood) != captured["missing_wood_units"]:
                    raise EconomyError(UNSUPPORTED, "Quarry fixture requires the captured 822 whole wood shortage")
                money_cost = captured["goldCost"]
                costs = dict(costs, wood=resource.wood)
            elif shortages:
                if immediate:
                    raise EconomyError(UNSUPPORTED, "No captured House shortage price")
                raise EconomyError(10004, "Insufficient building resources")
            user = await self._one(db, "SELECT money FROM users WHERE id=?", (user_id,))
            if user["money"] < money_cost:
                raise EconomyError(900001, "Insufficient synthetic premium money")
            result = await self.resources._deduct(db, user_id, city_id, costs,
                "upgradeImmediatelyByGoldWhenLackRes" if immediate else "upgrade")
            end = integer(now + duration, "building completion time", 0) if not immediate else 0
            await db.execute("UPDATE users SET money=money-? WHERE id=?", (money_cost, user_id))
            if immediate:
                await db.execute("UPDATE buildings SET level=level+1,state=0,target_level=NULL WHERE id=?", (building["id"],))
                await self._apply_building_effects(db, user_id, city_id, bid)
            else:
                await db.execute("UPDATE buildings SET state=1,state_start_s=?,state_end_s=?,original_end_s=?,target_level=level+1 WHERE id=?", (now, end, end, building["id"]))
            after = await self._one(db, "SELECT * FROM buildings WHERE id=?", (building["id"],))
            await self.resources._audit(db, user_id, city_id, "building_upgrade",
                {"building": self._building_wire(building), "money": user["money"]},
                {"building": self._building_wire(after), "money": user["money"] - money_cost,
                 "resource_audit_id": result.audit_id}, {"money": money_cost})
            response = {"building": self._building_wire(after), "cityResource": result.resources.to_wire()}
            if immediate:
                response["goldCost"] = money_cost
            return response

    async def building_info(self, user_id: int, city_id: int, unique_id: int) -> dict[str, Any]:
        integer(unique_id, "uniqueId")
        async with self.resources._transaction() as db:
            await self._require_city(db, user_id, city_id)
            now = self._now()
            await self._settle(db, user_id, city_id, now)
            building = await self._one(db, "SELECT * FROM buildings WHERE id=? AND cid=?", (unique_id, city_id))
            if building is None:
                raise EconomyError(900102, "Building is not owned by this city")
            if building["bid"] == 9:
                return {"building": self._building_wire(building), **await self._barracks_view(db, building, now)}
            current = self._buildings.get((building["bid"], building["level"]))
            if current is None:
                inspection = await self._inspection_view(db, building)
                if inspection is not None:
                    return inspection
                raise EconomyError(UNSUPPORTED, "Building has no captured information")
            if building["level"] == 2:
                preview = self._profile["quarry"]["level2_view"] if building["bid"] == 3 else self._profile["house_level2_view"]
                return {"building": self._building_wire(building), **deepcopy(preview)}
            following = self._buildings[(building["bid"], building["level"] + 1)]
            return {"building": self._building_wire(building),
                    "currBuilding": deepcopy(current["wire_info"]), "nextBuilding": deepcopy(following["wire_info"])}

    async def all_queues(self, user_id: int, city_id: int) -> dict[str, Any]:
        async with self.resources._transaction() as db:
            await self._require_city(db, user_id, city_id)
            now = self._now()
            await self._settle(db, user_id, city_id, now)
            drafts = await self._all(db, "SELECT q.*,b.xy FROM draft_queues q JOIN buildings b ON b.id=q.building_id WHERE q.cid=? AND q.state<>2 ORDER BY q.qid", (city_id,))
            buildings = await self._all(db, "SELECT * FROM buildings WHERE cid=? AND state=1 ORDER BY id", (city_id,))
            return {"buildingQueue": [self._building_wire(row) for row in buildings],
                    "soldierQueue": [self._queue_wire(row, now) for row in drafts],
                    "defenceQueue": [], "escapeQueue": [], "technichQueue": [],
                    "troopQueue": [], "woundedQueue": []}

    async def check_queue(self, user_id: int, city_id: int, queue_id: int, queue_type: int) -> dict[str, Any]:
        integer(queue_id, "queueId")
        integer(queue_type, "queueType")
        if queue_type not in (1, 3):
            raise EconomyError(UNSUPPORTED, "Only captured draft/building queue types are supported")
        async with self.resources._transaction() as db:
            await self._require_city(db, user_id, city_id)
            await self._settle(db, user_id, city_id, self._now())
            if queue_type == 1:
                queue = await self._one(db, "SELECT state FROM draft_queues WHERE qid=? AND cid=?", (queue_id, city_id))
                if queue is None:
                    raise EconomyError(900102, "Queue is not owned by this city")
                return {"queueId": queue_id, "queueType": 1, "needTry": queue["state"] != 2}
            building = await self._one(db, "SELECT * FROM buildings WHERE id=? AND cid=?", (queue_id, city_id))
            if building is None:
                raise EconomyError(900102, "Building queue is not owned by this city")
            result = {"queueId": queue_id, "queueType": 3, "needTry": building["state"] != 0}
            if building["state"] == 0:
                result["building"] = self._building_wire(building)
            return result

    async def building_state(self, user_id: int, city_id: int) -> dict[str, Any]:
        """The distinct getBuildingState queue shape, evidenced at capture14485."""
        async with self.resources._transaction() as db:
            await self._require_city(db, user_id, city_id)
            now = self._now()
            await self._settle(db, user_id, city_id, now)
            rows = await self._all(db, "SELECT q.*,b.xy FROM draft_queues q JOIN buildings b ON b.id=q.building_id WHERE q.cid=? AND q.state<>2 ORDER BY q.qid", (city_id,))
            queues = []
            for row in rows:
                if row["state"] != 1:
                    continue
                queues.append({"cid": city_id, "count": row["soldier_count"],
                    "draft_interval": self._armies[row["sid"]]["draftTime"], "id": row["qid"],
                    "original_endtime": row["end_s"], "queueCount": sum(q["building_id"] == row["building_id"] for q in rows),
                    "queueLimit": self._draft["queue_limit"], "queuetime": row["start_s"],
                    "reserved": 0, "sid": row["sid"], "sname": self._armies[row["sid"]]["sname"],
                    "state": 1, "state_endtime": row["end_s"], "state_starttime": row["start_s"], "xy": row["xy"]})
            return {"captiveHeroCount": 0, "escapedNum": 0, "market": {"myCount": 0},
                    "soldierQueue": queues, "starHeroAppears": 0, "starHeroNextTime": 0,
                    "trainHorseEndtime": 0, "unionTroop": {"troopNum": 0},
                    "woundedNum": 0, "woundedQueueNum": 0}
