"""Atomic local economy with the fields decoded by MemCityResource::decode.

Source: GhidraProjects/reports/gold_logic_focus.c:208-309. The captured
`vacation` extension is preserved separately from those 32 decoded fields.
Costs originate in server-loaded tables, never in a request. SQLite and the
attached audit database use rollback journals to commit state and audit together.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, fields
import hashlib
import hmac
from importlib.resources import files
import json
import math
from pathlib import Path
import secrets
import time
from typing import Any

import aiosqlite
import yaml

from .time_util import float32, signed

APPLICATION_ID = 1163350098

BALANCES = frozenset({"food", "gold", "wood", "iron", "rock", "people"})
FLOAT_FIELDS = frozenset({
    "food", "foodAdd", "foodArmyUse", "foodMax", "gold", "goldMax", "heroFee",
    "iron", "ironAdd", "ironMax", "rock", "rockAdd", "rockMax", "wood", "woodAdd", "woodMax",
})


class EconomyError(ValueError):
    """An expected failed operation; local codes are documented in README."""

    def __init__(self, ret: int, message: str) -> None:
        super().__init__(message)
        self.ret = ret


@dataclass(frozen=True, slots=True)
class CityResource:
    """Client float32/int32/int64 fields; defaults describe an empty test city."""

    changing: int = 0
    chiefLoyalty: int = 0
    cid: int = 1
    complaint: int = 0
    food: float = 0.0
    foodAdd: float = 0.0
    foodArmyUse: float = 0.0
    foodArmyUseRate: float = 0.0  # asInt(), then stored as float in client.
    foodMax: float = 0.0
    gold: float = 0.0
    goldMax: float = 0.0
    goldRate: int = 0
    heroFee: float = 0.0
    iron: float = 0.0
    ironAdd: float = 0.0
    ironMax: float = 0.0
    lastupdate: int = 0
    morale: int = 0
    moraleStable: int = 0
    people: int = 0
    peopleBuilding: int = 0
    peopleStable: int = 0
    peopleMax: int = 0
    peopleWorking: int = 0
    rock: float = 0.0
    rockAdd: float = 0.0
    rockMax: float = 0.0
    tax: int = 0
    wood: float = 0.0
    woodAdd: float = 0.0
    woodMax: float = 0.0
    vacation: int = 0

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name in FLOAT_FIELDS:
                if type(value) not in (int, float):
                    raise ValueError(f"{field.name} must be numeric")
                object.__setattr__(self, field.name, float32(value))
            elif field.name == "foodArmyUseRate":
                if type(value) not in (int, float) or not math.isfinite(value) or int(value) != value:
                    raise ValueError("foodArmyUseRate must represent an integer")
                rounded = float32(signed(int(value)))
                # asInt -> float can round INT32_MAX up to 2**31. Refuse a
                # snapshot that could not subsequently be decoded asInt again.
                signed(int(rounded))
                object.__setattr__(self, field.name, rounded)
            else:
                signed(value, 64 if field.name == "lastupdate" else 32)
            if field.name in BALANCES and value < 0:
                raise ValueError(f"negative {field.name} balance")
        if self.cid < 1:
            raise ValueError("city id must be positive")

    def to_wire(self) -> dict[str, int | float]:
        """Use integer input for foodArmyUseRate, as the client asInt requires."""
        result = asdict(self)
        result["foodArmyUseRate"] = int(self.foodArmyUseRate)
        return result


@dataclass(frozen=True, slots=True)
class DeductionResult:
    """Committed resource snapshot and the corresponding audit event id."""

    resources: CityResource
    audit_id: int


def _password_hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000).hex()


def load_troop_costs(path: Path | None = None) -> dict[int, dict[str, int]]:
    """Load captured base costs only, without inferring modifiers/queue timing."""
    source = path.read_text(encoding="utf-8") if path else files(__package__).joinpath("troop_config.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(source)
    if not isinstance(data, dict) or not isinstance(data.get("troops"), dict):
        raise ValueError("troop configuration requires a troops mapping")
    return _validated_troops(data["troops"])


def _validated_troops(data: Mapping[int, Mapping[str, int]]) -> dict[int, dict[str, int]]:
    result: dict[int, dict[str, int]] = {}
    for sid, costs in data.items():
        if type(sid) is not int or not 1 <= sid <= 2**31 - 1 or not isinstance(costs, dict):
            raise ValueError("invalid troop configuration")
        if set(costs) != BALANCES or any(type(v) is not int or not 0 <= v <= 2**31 - 1 for v in costs.values()):
            raise ValueError("every troop requires six nonnegative integer resource costs")
        result[sid] = dict(costs)
    return result


class ResourceEngine:
    """One connection per operation; BEGIN IMMEDIATE serializes competing writers.

    A transaction's state change, optional training reservation, and audit entry
    commit together. This is a synthetic ledger: passive production, player
    bonuses, draft completion and building cost formulas are not inferred.
    """

    def __init__(self, database: Path, audit_database: Path,
                 troop_costs: Mapping[int, Mapping[str, int]] | None = None) -> None:
        self.database = Path(database).resolve()
        self.audit_database = Path(audit_database).resolve()
        if self.database == self.audit_database:
            raise ValueError("state and audit must be different files")
        self.troop_costs = _validated_troops(load_troop_costs() if troop_costs is None else troop_costs)

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[aiosqlite.Connection]:
        async with aiosqlite.connect(self.database, isolation_level=None, timeout=5.0) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("PRAGMA busy_timeout=5000")
            await db.execute("ATTACH DATABASE ? AS audit", (str(self.audit_database),))
            for schema in ("main", "audit"):
                async with db.execute(f"PRAGMA {schema}.journal_mode") as cursor:
                    mode = (await cursor.fetchone())[0]
                if mode.lower() != "delete":
                    raise RuntimeError("state/audit databases require DELETE journal mode")
                await db.execute(f"PRAGMA {schema}.synchronous=FULL")
            yield db

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    async def initialize(self) -> None:
        """Atomically initialize/migrate the owned state and audit database pair.

        Existing accounts, resources and audit records are never reset. A missing
        counterpart, mismatched version or unowned database fails before opening
        either file for writing. Versions are checked again under the write lock.
        """
        from sqlite3 import complete_statement  # SQL parser only; no synchronous DB I/O.

        current_version = 6

        async def metadata(db: aiosqlite.Connection, schema: str) -> tuple[int, int, bool]:
            async with db.execute(f"PRAGMA {schema}.application_id") as cursor:
                application_id = (await cursor.fetchone())[0]
            async with db.execute(f"PRAGMA {schema}.user_version") as cursor:
                version = (await cursor.fetchone())[0]
            async with db.execute(f"SELECT COUNT(*) FROM {schema}.sqlite_master") as cursor:
                empty = (await cursor.fetchone())[0] == 0
            return application_id, version, empty

        def checked_version(info: tuple[int, int, bool]) -> int:
            application_id, version, empty = info
            if application_id == 0 and version == 0 and empty:
                return 0  # Pristine DB, including a rolled-back fresh migration.
            if application_id != APPLICATION_ID:
                raise ValueError("refusing to modify a database not owned by this emulator")
            if not 1 <= version <= current_version:
                raise ValueError("unsupported emulator schema version")
            return version

        if self.database.exists() and self.audit_database.exists() and self.database.samefile(self.audit_database):
            raise ValueError("state and audit must be different files")
        versions: list[int] = []
        for path in (self.database, self.audit_database):
            if not path.exists() or path.stat().st_size == 0:
                versions.append(0)
            else:
                async with aiosqlite.connect(path.as_uri() + "?mode=ro", uri=True) as existing:
                    versions.append(checked_version(await metadata(existing, "main")))
        if versions[0] != versions[1]:
            raise ValueError("state/audit schema mismatch or missing database counterpart")

        scripts = {
            1: files("empire_war_private_server").joinpath("migrations/001_sqlite.sql").read_text(encoding="utf-8"),
            2: files("empire_war_private_server").joinpath("migrations/002_gameplay.sql").read_text(encoding="utf-8"),
            3: files("empire_war_private_server").joinpath("migrations/003_wire_time_seconds.sql").read_text(encoding="utf-8"),
            4: files("empire_war_private_server").joinpath("migrations/004_market.sql").read_text(encoding="utf-8"),
            5: files("empire_war_private_server").joinpath("migrations/005_online_rewards.sql").read_text(encoding="utf-8"),
            6: files("empire_war_private_server").joinpath("migrations/006_signins.sql").read_text(encoding="utf-8"),
        }
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.audit_database.parent.mkdir(parents=True, exist_ok=True)
        async with self._transaction() as db:
            locked_versions = [checked_version(await metadata(db, schema)) for schema in ("main", "audit")]
            if locked_versions[0] != locked_versions[1]:
                raise ValueError("state/audit schema mismatch or missing database counterpart")
            for target_version in range(locked_versions[0] + 1, current_version + 1):
                statement = ""
                for line in scripts[target_version].splitlines(keepends=True):
                    statement += line
                    if complete_statement(statement):
                        await db.execute(statement)
                        statement = ""
                if statement.strip():
                    raise ValueError("incomplete migration SQL statement")
            final_versions = [checked_version(await metadata(db, schema)) for schema in ("main", "audit")]
            if final_versions != [current_version, current_version]:
                raise ValueError("migration did not set matching schema versions")

    async def seed_synthetic_user(self, passport: str, password: str,
                                  resources: CityResource) -> None:
        """Idempotently initialize one synthetic user; never overwrite balances."""
        if not passport or not password or len(passport) > 128 or len(password) > 256:
            raise ValueError("invalid synthetic credentials")
        salt = secrets.token_bytes(16)
        password_hash = await asyncio.to_thread(_password_hash, password, salt)
        async with self._transaction() as db:
            async with db.execute("SELECT id,passport,password_salt,password_hash FROM users WHERE id=1") as cursor:
                existing = await cursor.fetchone()
            if existing is not None:
                candidate = await asyncio.to_thread(_password_hash, password, bytes.fromhex(existing["password_salt"]))
                if existing["passport"] != passport or not hmac.compare_digest(candidate, existing["password_hash"]):
                    raise ValueError("synthetic account already exists with different credentials")
                return
            await db.execute("INSERT INTO users(id,passport,password_salt,password_hash) VALUES (1,?,?,?)",
                             (passport, salt.hex(), password_hash))
            values = resources.to_wire()
            columns = ",".join(values)
            marks = ",".join("?" for _ in values)
            await db.execute(f"INSERT INTO cities(user_id,{columns}) VALUES (1,{marks})", tuple(values.values()))
            await self._audit(db, 1, resources.cid, "synthetic_seed", {}, values, {})

    async def authenticate(self, passport: str, password: str) -> tuple[int, int] | None:
        """Validate synthetic login without storing or logging cleartext passwords."""
        if not passport or not password or len(passport) > 128 or len(password) > 256:
            return None
        async with self._connection() as db:
            async with db.execute("SELECT u.*,c.cid FROM users u JOIN cities c ON c.user_id=u.id WHERE passport=? ORDER BY c.cid LIMIT 1", (passport,)) as cursor:
                row = await cursor.fetchone()
        salt = bytes.fromhex(row["password_salt"]) if row else bytes(16)
        candidate = await asyncio.to_thread(_password_hash, password, salt)
        if row and hmac.compare_digest(candidate, row["password_hash"]):
            return row["id"], row["cid"]
        return None

    @staticmethod
    async def _read(db: aiosqlite.Connection, user_id: int, city_id: int) -> CityResource:
        async with db.execute("SELECT * FROM cities WHERE cid=? AND user_id=?", (city_id, user_id)) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise EconomyError(900102, "City is not owned by this session")
        return CityResource(**{field.name: row[field.name] for field in fields(CityResource)})

    async def get_resources(self, user_id: int, city_id: int) -> CityResource:
        """Return a consistent snapshot; no unverified passive production formula."""
        async with self._connection() as db:
            return await self._read(db, user_id, city_id)

    @staticmethod
    async def _audit(db: aiosqlite.Connection, user_id: int, city_id: int, action: str,
                     before: Mapping[str, Any], after: Mapping[str, Any], costs: Mapping[str, Any]) -> int:
        async with db.execute(
            "INSERT INTO audit.economy_events(created_ms,user_id,city_id,action,before_json,after_json,costs_json) VALUES (?,?,?,?,?,?,?)",
            (time.time_ns() // 1_000_000, user_id, city_id, action,
             json.dumps(dict(before), allow_nan=False), json.dumps(dict(after), allow_nan=False),
             json.dumps(dict(costs), allow_nan=False)),
        ) as cursor:
            assert cursor.lastrowid is not None
            return cursor.lastrowid

    @staticmethod
    def _validate_costs(costs: Mapping[str, int | float]) -> dict[str, int | float]:
        if not costs or not set(costs) <= BALANCES:
            raise EconomyError(900103, "Unknown or empty resource cost")
        result = dict(costs)
        for name, value in result.items():
            if type(value) not in (int, float):
                raise EconomyError(900103, "Resource costs must be numeric")
            try:
                valid = math.isfinite(value) and 0 <= value <= 2**31 - 1
            except OverflowError:
                valid = False
            if not valid or (name == "people" and type(value) is not int):
                raise EconomyError(900103, "Invalid resource cost")
        return result

    async def _deduct(self, db: aiosqlite.Connection, user_id: int, city_id: int,
                      costs: Mapping[str, int | float], action: str) -> DeductionResult:
        before = await self._read(db, user_id, city_id)
        # Check the entire cost vector before any UPDATE or audit insertion.
        if any(getattr(before, name) < amount for name, amount in costs.items()):
            raise EconomyError(13003, "Insufficient troop resources")
        values = before.to_wire()
        for name, amount in costs.items():
            values[name] -= amount
        values["lastupdate"] = time.time_ns() // 1_000_000_000
        after = CityResource(**values)
        for name, amount in costs.items():
            # At very large float32 balances a small subtraction can disappear.
            # Refuse it rather than silently issue free resources in the lab.
            if amount and getattr(after, name) >= getattr(before, name):
                raise EconomyError(900103, "Deduction is below float32 balance precision")
        stored = after.to_wire()
        columns = ",".join(f"{name}=?" for name in stored)
        await db.execute(f"UPDATE cities SET {columns} WHERE cid=? AND user_id=?",
                         (*stored.values(), city_id, user_id))
        audit_id = await self._audit(db, user_id, city_id, action, before.to_wire(), stored, costs)
        return DeductionResult(after, audit_id)

    async def validate_and_deduct_resources(self, user_id: int, city_id: int,
                                             costs: Mapping[str, int | float], *,
                                             action: str = "internal_deduction") -> DeductionResult:
        """Trusted internal API only. All checks and deductions commit atomically."""
        normalized = self._validate_costs(costs)
        if not action or len(action) > 128:
            raise ValueError("invalid audit action")
        async with self._transaction() as db:
            return await self._deduct(db, user_id, city_id, normalized, action)

    async def reserve_troops(self, user_id: int, city_id: int, sid: int,
                             soldier_count: int) -> DeductionResult:
        """Reserve at captured base costs, as an internal Phase 2 building block.

        Not exposed as a successful startDraftQueue wire handler: queue timing,
        building prerequisites and server modifiers still need validation.
        No request deduplication: each successful call is a separate reservation.
        """
        if type(sid) is not int or sid not in self.troop_costs:
            raise EconomyError(900103, "Unknown troop sid")
        if type(soldier_count) is not int or not 1 <= soldier_count <= 2**31 - 1:
            raise EconomyError(900103, "Invalid soldierCount")
        costs = self._validate_costs({name: value * soldier_count for name, value in self.troop_costs[sid].items()})
        async with self._transaction() as db:
            result = await self._deduct(db, user_id, city_id, costs, "reserve_troops")
            await db.execute("INSERT INTO training_reservations(user_id,city_id,sid,soldier_count,audit_id) VALUES (?,?,?,?,?)",
                             (user_id, city_id, sid, soldier_count, result.audit_id))
            return result
