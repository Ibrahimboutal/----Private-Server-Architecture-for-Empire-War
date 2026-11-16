"""Captured reward offers with atomic credits to the synthetic city ledger.

The six observed transitions are supported. Countdown uses persisted wall time
starting at the first local login-info query; this is local test policy, not an
inferred online-time or daily-reset formula. Final-pack completion is unobserved.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from functools import lru_cache
from importlib.resources import files
import json
import math
import time
from typing import Any

import aiosqlite

from .resource_engine import CityResource, EconomyError, ResourceEngine

RESOURCE_NAMES = {1: "food", 2: "wood", 3: "rock", 4: "iron", 5: "gold"}


@lru_cache(maxsize=1)
def _fixture() -> dict[str, Any]:
    data = json.loads(files("empire_war_private_server").joinpath(
        "data/online_rewards.json").read_text(encoding="utf-8"))
    if data["version"] != 1 or len(data["stages"]) != 7 or set(data["claim_rows"]) != set("012345"):
        raise ValueError("unsupported reward fixture")
    return data


def reward_goods_catalog() -> dict[int, dict[str, Any]]:
    """Return independent public item descriptors; counts are not ownership."""
    return {item["gid"]: dict(deepcopy(item), count=0)
            for stage in _fixture()["stages"] for item in stage["goodsList"]}


class OnlineRewardEngine:
    """Serialize claims with the economy lock; reconnects never reset progress."""

    def __init__(self, resources: ResourceEngine, *, clock: Callable[[], float] = time.time) -> None:
        self.resources = resources
        self.clock = clock

    def _now(self) -> int:
        value = self.clock()
        if not math.isfinite(value) or not 0 <= value <= 2**31 - 1 - 10800:
            raise EconomyError(900103, "Reward clock is outside client timestamp range")
        return int(value)

    async def _state(self, db: aiosqlite.Connection, city_id: int, now: int) -> aiosqlite.Row:
        await db.execute("INSERT INTO online_reward_state(city_id,step,ready_at_s) VALUES (?,0,?) "
                         "ON CONFLICT(city_id) DO NOTHING", (city_id, now + _fixture()["stages"][0]["needOnlineTime"]))
        async with db.execute("SELECT step,ready_at_s FROM online_reward_state WHERE city_id=?", (city_id,)) as cursor:
            row = await cursor.fetchone()
        assert row is not None
        return row

    @staticmethod
    def _wire(step: int, ready_at: int, now: int) -> dict[str, int]:
        return {"step": step, "timeLeft": max(0, ready_at - now), "nextOnlineRewardTime": ready_at}

    async def info(self, user_id: int, city_id: int, *, include_catalog: bool = True) -> dict[str, Any]:
        """Read current progress and initialize its countdown once, if needed."""
        async with self.resources._transaction() as db:
            await self.resources._read(db, user_id, city_id)
            now = self._now()
            state = await self._state(db, city_id, now)
            result: dict[str, Any] = self._wire(state["step"], state["ready_at_s"], now)
            if include_catalog:
                result["onlineRewardInfoList"] = deepcopy(_fixture()["stages"])
            return result

    async def claim(self, user_id: int, city_id: int) -> dict[str, int]:
        """Credit one due, observed reward; duplicates during cooldown do nothing.

        Captured claims contain no request ID or stage. The durable city/stage
        key prevents re-awarding any stage. An early retry returns current state,
        matching the claim response shape without performing another mutation.
        """
        async with self.resources._transaction() as db:
            before = await self.resources._read(db, user_id, city_id)
            now = self._now()
            state = await self._state(db, city_id, now)
            step, ready_at = state["step"], state["ready_at_s"]
            if now < ready_at:
                return self._wire(step, ready_at, now)
            if str(step) not in _fixture()["claim_rows"]:
                raise EconomyError(900105, "Final online reward transition is not captured")
            offer = _fixture()["stages"][step]
            values = before.to_wire()
            credits = {RESOURCE_NAMES[item["type"]]: item["count"] for item in offer["resList"]}
            for name, count in credits.items():
                values[name] += count
            values["lastupdate"] = now
            try:
                after = CityResource(**values).to_wire()
            except (ValueError, OverflowError) as exc:
                raise EconomyError(900103, "Reward exceeds resource bounds") from exc
            if any(after[name] <= getattr(before, name) for name in credits):
                raise EconomyError(900103, "Reward exceeds resource precision")
            async with db.execute("SELECT gid,count FROM city_goods WHERE city_id=?", (city_id,)) as cursor:
                old_goods = {row["gid"]: row["count"] for row in await cursor.fetchall()}
            new_goods = dict(old_goods)
            for item in offer["goodsList"]:
                gid = item["gid"]
                new_goods[gid] = new_goods.get(gid, 0) + item["count"]
                if new_goods[gid] > 2**31 - 1:
                    raise EconomyError(900103, "Reward exceeds inventory count range")
            await db.execute("INSERT INTO online_reward_claims(city_id,step,claimed_at_s) VALUES (?,?,?)",
                             (city_id, step, now))
            columns = ",".join(f"{name}=?" for name in credits)
            await db.execute(f"UPDATE cities SET {columns},lastupdate=? WHERE cid=? AND user_id=?",
                             (*(after[name] for name in credits), now, city_id, user_id))
            for item in offer["goodsList"]:
                await db.execute("INSERT INTO city_goods(city_id,gid,count) VALUES (?,?,?) "
                                 "ON CONFLICT(city_id,gid) DO UPDATE SET count=excluded.count",
                                 (city_id, item["gid"], new_goods[item["gid"]]))
            next_step = step + 1
            ready_at = now + _fixture()["stages"][next_step]["needOnlineTime"]
            await db.execute("UPDATE online_reward_state SET step=?,ready_at_s=? WHERE city_id=?",
                             (next_step, ready_at, city_id))
            await self.resources._audit(db, user_id, city_id, "getOnlineReward",
                {"resources": before.to_wire(), "goods": old_goods, "step": step},
                {"resources": after, "goods": new_goods, "step": next_step},
                {"resources": credits, "goods": {item["gid"]: item["count"] for item in offer["goodsList"]}})
            return self._wire(next_step, ready_at, now)
