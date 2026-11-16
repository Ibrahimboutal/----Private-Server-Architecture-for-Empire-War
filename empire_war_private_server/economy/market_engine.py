"""Captured listing fixture and durable, bounded local overflow experiments.

The supplied arithmetic guard is a research model, not an official price
formula. The sole successful listing scenario is extracted from rows
19126/19127; its food-only debit is isolated by equal-duration resource samples.
Buying, cancellation, settlement, caravan limits and expiry are not inferred.
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

from .resource_engine import EconomyError, ResourceEngine
from .time_util import float32
from ..vuln_research import PRICE_OVERFLOW, calculate_market_price_safe

MARKET_PROFILE_ID = "captured-market-food-v1"
MAX_LOCAL_LISTINGS = 128  # Local storage/response bound, not a recovered caravan limit.


class SimulatedMarketDisconnect(Exception):
    """After the audit commits, the gateway delays then closes this connection.

    This never terminates the process or blocks its event loop. The delay is
    capped at one second; other connections and queue workers remain available.
    """

    def __init__(self, delay_ms: int) -> None:
        super().__init__("bounded local market failure simulation")
        self.delay_ms = delay_ms


def _redact(value: Any, depth: int = 0) -> Any:
    """Keep decoded research fields while removing supplied credential fields.

    The gateway already restricts input to bounded, finite JSON. Tagged values
    also make direct Python calls containing NaN/Infinity or huge integers
    loggable without writing invalid JSON. Raw encrypted packets are not stored
    because they would retain any credentials that this function redacts.
    """
    if depth > 100:
        return "[depth limit]"
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            normalized = "".join(c for c in str(key).lower() if c.isalnum())
            secret = normalized in {"passport", "authorization", "cookie", "gameuuid", "sessionid"}
            secret = secret or normalized.endswith(("password", "token", "secret", "cookie"))
            result[str(key)] = "[redacted]" if secret else _redact(item, depth + 1)
        return result
    if isinstance(value, list):
        return [_redact(item, depth + 1) for item in value]
    if type(value) is float and not math.isfinite(value):
        return {"nonfinite": str(value)}
    if type(value) is int and value.bit_length() > 14000:
        return {"integer_bits": value.bit_length(), "negative": value < 0}
    if value is None or type(value) in (str, int, float, bool):
        return value
    return {"unsupported_type": type(value).__name__}


class MarketEngine:
    """One transaction covers a listing, its charge, audit and breaker state."""

    def __init__(self, resources: ResourceEngine, *, simulate_int32_crash: bool = False,
                 overflow_breaker_threshold: int = 3, simulation_delay_ms: int = 100,
                 clock: Callable[[], float] = time.time) -> None:
        if type(simulate_int32_crash) is not bool:
            raise ValueError("simulate_int32_crash must be boolean")
        if type(overflow_breaker_threshold) is not int or not 1 <= overflow_breaker_threshold <= 100:
            raise ValueError("overflow_breaker_threshold must be 1..100")
        if type(simulation_delay_ms) is not int or not 0 <= simulation_delay_ms <= 1000:
            raise ValueError("simulation_delay_ms must be 0..1000")
        self.resources = resources
        self.simulate_int32_crash = simulate_int32_crash
        self.overflow_breaker_threshold = overflow_breaker_threshold
        self.simulation_delay_ms = simulation_delay_ms
        self.clock = clock
        self._profile = json.loads(files("empire_war_private_server").joinpath(
            "data/market_scenario.json").read_text(encoding="utf-8"))
        if self._profile["version"] != 1 or self._profile["profile_id"] != MARKET_PROFILE_ID:
            raise ValueError("unknown market fixture")
        if self._profile["costs"] != {"food": 1}:
            raise ValueError("unexpected market fixture cost")

    @staticmethod
    async def _one(db: aiosqlite.Connection, sql: str, args: tuple = ()) -> aiosqlite.Row:
        async with db.execute(sql, args) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("required market state is missing")
        return row

    @staticmethod
    def _response(action: str, ret: int = 0, **body: Any) -> dict[str, Any]:
        return {"type": "Market", "action": action, "ret": ret, **body}

    async def _owned(self, db: aiosqlite.Connection, user_id: int, city_id: int) -> None:
        if type(user_id) is not int or type(city_id) is not int or min(user_id, city_id) < 1:
            raise EconomyError(900102, "City is not owned by this session")
        await self.resources._read(db, user_id, city_id)

    async def research_status(self) -> dict[str, Any]:
        """Internal inspection API; this is not an unauthenticated wire action."""
        async with self.resources._connection() as db:
            row = await self._one(db, "SELECT * FROM market_research_state WHERE singleton=1")
        return {"configured_simulation": self.simulate_int32_crash,
                "simulation_enabled": self.simulate_int32_crash and not row["breaker_tripped"],
                "breaker_tripped": bool(row["breaker_tripped"]),
                "consecutive_overflows": row["consecutive_overflows"],
                "threshold": self.overflow_breaker_threshold}

    async def _event(self, db: aiosqlite.Connection, user_id: int, city_id: int,
                     request: dict[str, Any], metadata: dict[str, int] | None,
                     outcome: str, ret: int, total: int | None, simulated: bool,
                     tripped: bool, consecutive: int) -> None:
        frame = {key: value for key, value in (metadata or {}).items()
                 if key in {"packet_type", "wire_length", "plaintext_length"} and type(value) is int}
        await db.execute(
            "INSERT INTO audit.market_events(created_ms,user_id,city_id,packet_json,frame_json,"
            "outcome,ret,total_value,simulated,breaker_tripped,consecutive_overflows) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (time.time_ns() // 1_000_000, user_id, city_id,
             json.dumps(_redact(request), allow_nan=False, ensure_ascii=True),
             json.dumps(frame), outcome, ret, total, int(simulated), int(tripped), consecutive))

    async def sell_to_user(self, user_id: int, city_id: int, request: dict[str, Any], *,
                           frame_metadata: dict[str, int] | None = None,
                           wire_city_id: int | None = None) -> dict[str, Any]:
        """Validate ownership first, then log and reject or commit one fixture sale.

        The Nth consecutive overflow in simulation mode is recorded, trips the
        breaker, and simulates one final connection failure. Later requests use
        the safe response even after restart. A valid non-overflow arithmetic
        request resets the consecutive counter only before a trip. A new state/
        audit pair is required to rearm a tripped experiment.
        """
        simulated = False
        async with self.resources._transaction() as db:
            await self._owned(db, user_id, city_id)
            expected = city_id if wire_city_id is None else wire_city_id
            if type(request.get("cityId")) is not int or request["cityId"] != expected:
                raise EconomyError(900102, "City is not owned by this session")
            state = await self._one(db, "SELECT * FROM market_research_state WHERE singleton=1")
            count, tripped = state["consecutive_overflows"], bool(state["breaker_tripped"])
            gold, hour, res_count = request.get("gold"), request.get("hour"), request.get("resCount")
            checked = calculate_market_price_safe(gold, hour, res_count)
            total = checked.get("total_value")
            ret = checked["ret"]
            outcome = "invalid"
            response = self._response("sellToUser", ret, **({"msg": checked["msg"]} if "msg" in checked else {}))
            if ret == PRICE_OVERFLOW:
                count = min(count + 1, 2**31 - 1)
                simulated = self.simulate_int32_crash and not tripped
                tripped = tripped or (simulated and count >= self.overflow_breaker_threshold)
                outcome = "overflow"
            elif ret == 0:
                # These are local input-domain rules; the pure guard retains
                # signed/zero vectors for arithmetic boundary research.
                valid = (gold > 0 and hour > 0 and 1 <= res_count <= 2**31 - 1
                         and type(request.get("resType")) is int
                         and type(request.get("unionSel")) is int)
                if not valid:
                    response = self._response("sellToUser", 900103, msg="Invalid market request")
                else:
                    if not tripped:
                        count = 0
                    expected = self._profile["request"]
                    try:
                        supported = all(request.get(key) == expected[key] for key in ("resType", "resCount", "unionSel"))
                        supported = supported and float32(gold) == float32(expected["gold"]) and float32(hour) == float32(expected["hour"])
                    except ValueError:
                        supported = False
                    if not supported:
                        outcome = "unsupported"
                        response = self._response("sellToUser", 900105, msg="No captured listing scenario for these values")
                    else:
                        response, outcome = await self._list_fixture(db, user_id, city_id)
            await db.execute("UPDATE market_research_state SET consecutive_overflows=?,breaker_tripped=? WHERE singleton=1",
                             (count, int(tripped)))
            await self._event(db, user_id, city_id, request, frame_metadata, outcome,
                              response["ret"], total, simulated, tripped, count)
        # Do not delay or close a connection until its overflow event and breaker
        # transition commit. No database lock is held while simulating failure.
        if simulated:
            raise SimulatedMarketDisconnect(self.simulation_delay_ms)
        return response

    async def _list_fixture(self, db: aiosqlite.Connection, user_id: int,
                            city_id: int) -> tuple[dict[str, Any], str]:
        resource = await self.resources._read(db, user_id, city_id)
        if resource.food < 1:
            return self._response("sellToUser", 19004, msg="Insufficient Food"), "insufficient"
        size = await self._one(db, "SELECT COUNT(*) AS count FROM market_listings WHERE cid=?", (city_id,))
        if size["count"] >= MAX_LOCAL_LISTINGS:
            return self._response("sellToUser", 900106, msg="Local listing fixture limit reached"), "limited"
        now = self.clock()
        if type(now) not in (int, float) or not math.isfinite(now) or not 0 <= now <= 2**31 - 1:
            raise ValueError("market clock must fit Unix seconds in int32")
        try:
            charge = await self.resources._deduct(db, user_id, city_id, self._profile["costs"], "sellToUser")
        except EconomyError as exc:
            # _deduct validates before writing, including float32 precision.
            return self._response("sellToUser", exc.ret, msg=str(exc)), "invalid"
        trade = deepcopy(self._profile["trade_template"])
        trade.update(cid=city_id, generateTime=int(now))
        async with db.execute(
            "INSERT INTO market_listings(user_id,cid,res_type,res_count,gold,hour,unit_price,"
            "generate_time_s,limit_time_s,state,trade_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, city_id, trade["restype"], trade["count"], trade["gold"], self._profile["request"]["hour"],
             trade["price"], trade["generateTime"], trade["limittime"], trade["state"], "{}")) as cursor:
            trade["id"] = cursor.lastrowid
        if type(trade["id"]) is not int or not 1 <= trade["id"] <= 2**31 - 1:
            # Roll back the charge and row as well; the native decoder reads id
            # as int32 even though SQLite's generated rowid can be wider.
            raise EconomyError(900103, "Listing identifier exceeds native bounds")
        await db.execute("UPDATE market_listings SET trade_json=? WHERE id=?",
                         (json.dumps(trade, allow_nan=False), trade["id"]))
        await self.resources._audit(db, user_id, city_id, "market_listing", {},
                                    {"trade": trade, "resource_audit_id": charge.audit_id}, {})
        return self._response("sellToUser", trade=trade), "listed"

    async def own_trades(self, user_id: int, city_id: int) -> dict[str, Any]:
        """Return the captured own-list shape; expired lifecycle is unsupported."""
        async with self.resources._transaction() as db:
            await self._owned(db, user_id, city_id)
            async with db.execute("SELECT trade_json FROM market_listings WHERE user_id=? AND cid=? ORDER BY id", (user_id, city_id)) as cursor:
                rows = await cursor.fetchall()
            trades = []
            for row in rows:
                trade = json.loads(row["trade_json"])
                if self.clock() >= trade["generateTime"] + trade["limittime"]:
                    return self._response("doGetCityTrade", 900105, msg="Listing expiry is outside this captured fixture")
                own = deepcopy(self._profile["own_list_template"])
                own.update({key: value for key, value in trade.items() if key in own})
                trades.append(own)
            return self._response("doGetCityTrade", tradingList=trades)

    async def user_sell_data(self, user_id: int, city_id: int) -> dict[str, Any]:
        """Captured informational ranges; only the selected listing is enabled."""
        async with self.resources._connection() as db:
            await self._owned(db, user_id, city_id)
        return self._response("getUserSellData", citySellData=deepcopy(self._profile["get_user_sell_data"]))

    async def listing_count(self, user_id: int, city_id: int) -> int:
        """Count retained local listings for getBuildingState.market.myCount."""
        async with self.resources._transaction() as db:
            await self._owned(db, user_id, city_id)
            row = await self._one(db, "SELECT COUNT(*) AS count FROM market_listings WHERE user_id=? AND cid=?", (user_id, city_id))
            return row["count"]

    async def cancel_sell(self, user_id: int, city_id: int, trade_id: object) -> dict[str, Any]:
        """Cancel an owned local listing using the captured response shape."""
        if type(trade_id) is not int or not 1 <= trade_id <= 2**31 - 1:
            raise EconomyError(900103, "Invalid tradeId")
        async with self.resources._transaction() as db:
            await self._owned(db, user_id, city_id)
            async with db.execute(
                    "SELECT trade_json FROM market_listings WHERE id=? AND user_id=? AND cid=?",
                    (trade_id, user_id, city_id)) as cursor:
                row = await cursor.fetchone()
            if row is None:
                raise EconomyError(900102, "Trade is not owned by this session")
            trade = json.loads(row["trade_json"])
            await db.execute("DELETE FROM market_listings WHERE id=?", (trade_id,))
            await self._event(db, user_id, city_id,
                              {"type": "Market", "action": "cancelSell", "tradeId": trade_id},
                              None, "cancelled", 0, None, False, False, 0)
            return self._response("cancelSell", trade=trade)
