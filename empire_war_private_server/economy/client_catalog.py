"""Static client display catalogs extracted from successful captured logins.

These tables make native ConfigManager lookups available. They do not authorize
additional server actions, troops, upgrades, rewards or economy formulas. The
runtime loads a bundled sanitized JSON file and never opens a capture database.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from importlib.resources import files
import json
import re
from typing import Any

GAME_VERSION = 1719


@dataclass(frozen=True, slots=True)
class CatalogSchema:
    """Explicit captured field whitelist and stable entry identity."""

    identity: str | None
    count: int
    integers: tuple[str, ...]
    strings: tuple[str, ...] = ()


CATALOG_SCHEMAS: dict[str, CatalogSchema] = {
    "cfgBarracksLimitList": CatalogSchema("level", 10, ("level", "soldierLimit")),
    "cfgBuilding": CatalogSchema("bid", 20, ("bid", "inner"),
        ("description", "name", "summary", "tableName")),
    "cfgDefence": CatalogSchema("did", 5,
        ("ap", "areaNeed", "carry", "did", "dp", "foodNeed", "goldNeed", "hp", "ironNeed",
         "range", "rockNeed", "speed", "timeNeed", "type", "woodNeed"), ("description", "name")),
    "cfgHeroLevel": CatalogSchema("level", 100, ("level", "totalExp", "upgradeExp")),
    "cfgMonsterInfoList": CatalogSchema("level", 20, ("face", "level"), ("des", "name")),
    "cfgSoldier": CatalogSchema("sid", 99,
        ("ap", "carry", "dp", "foodNeed", "foodUse", "fromcity", "goldNeed", "hp",
         "ironNeed", "peopleNeed", "range", "rockNeed", "sid", "speed", "timeNeed", "type", "woodNeed"),
        ("description", "name")),
    "cfgTalentInfoList": CatalogSchema("id", 109, ("genWeight", "heroType", "id", "starLimit"),
        ("active_info", "description", "name")),
    "cfgTechnic": CatalogSchema("tid", 21, ("tid",), ("description", "name")),
    "cfgTrick": CatalogSchema("id", 28, ("cost", "id", "usetype"), ("description", "manual", "name")),
    # No populated union-tech catalog is present in the successful login set.
    "cfgUnionTechnic": CatalogSchema(None, 0, ()),
    "cfgVipPrivilegeList": CatalogSchema("id", 210, ("effectValue", "id", "level", "type")),
}

_FORBIDDEN_TEXT = re.compile(
    r"(?:[a-z][a-z0-9+.-]*://|www\.|\b(?:\d{1,3}\.){3}\d{1,3}\b|"
    r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}|"
    r"\b(?:password|passwd|passport|authorization|session[_ -]?id|access[_ -]?token|"
    r"refresh[_ -]?token|api[_ -]?key)\b)", re.IGNORECASE)


def validate_catalogs(catalogs: object) -> dict[str, list[dict[str, int | str]]]:
    """Reject unknown fields, identities, types and endpoint/credential text.

    The exact allowlists are intentional: an added field must be reviewed before
    a later capture can enter the bundled catalog. Only copied scalar fields are
    allowed, never nested account state or arbitrary JSON.
    """
    if type(catalogs) is not dict or set(catalogs) != set(CATALOG_SCHEMAS):
        raise ValueError("client catalog tables do not match the reviewed schema")
    result: dict[str, list[dict[str, int | str]]] = {}
    for name, schema in CATALOG_SCHEMAS.items():
        entries = catalogs[name]
        if type(entries) is not list or len(entries) != schema.count:
            raise ValueError(f"unexpected entry count in {name}")
        expected = set(schema.integers + schema.strings)
        identities: set[int] = set()
        clean = []
        for entry in entries:
            if type(entry) is not dict or set(entry) != expected:
                raise ValueError(f"unreviewed fields in {name}")
            for key in schema.integers:
                if type(entry[key]) is not int or not -(2**63) <= entry[key] < 2**63:
                    raise ValueError(f"invalid integer field {name}.{key}")
            for key in schema.strings:
                value = entry[key]
                if type(value) is not str or len(value) > 8192 or _FORBIDDEN_TEXT.search(value):
                    raise ValueError(f"unreviewed static text in {name}.{key}")
            if schema.identity is not None:
                identity = entry[schema.identity]
                if identity <= 0 or identity in identities:
                    raise ValueError(f"invalid or duplicate identity in {name}")
                identities.add(identity)
            clean.append({key: entry[key] for key in schema.integers + schema.strings})
        result[name] = clean
    return result


def load_client_catalog() -> dict[str, Any]:
    """Return a fresh complete login overlay with version and all eleven tables."""
    fixture = json.loads(files("empire_war_private_server").joinpath(
        "data/client_catalog.json").read_text(encoding="utf-8"))
    if fixture.get("version") != 1 or fixture.get("game_version") != GAME_VERSION:
        raise ValueError("unsupported client catalog version")
    return {"gameVersion": GAME_VERSION, **validate_catalogs(fixture.get("catalogs"))}


def merge_scenario_catalog(catalog: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    """Preserve the complete catalog when a scenario selects a small subset.

    Existing scenarios carry subsets of the same captured static configuration.
    They must agree with the catalog; quote modifiers belong in the scenario's
    action response, not in these base tables. Empty selections never erase a
    catalog, and no unsupported mutation is enabled by returning display data.
    """
    if set(catalog) != {"gameVersion", *CATALOG_SCHEMAS} or catalog["gameVersion"] != GAME_VERSION:
        raise ValueError("invalid complete client catalog")
    clean = {"gameVersion": GAME_VERSION, **validate_catalogs({
        name: catalog[name] for name in CATALOG_SCHEMAS})}
    if type(scenario) is not dict or not set(scenario) <= set(CATALOG_SCHEMAS):
        raise ValueError("scenario contains non-catalog fields")
    for name, selected in scenario.items():
        schema = CATALOG_SCHEMAS[name]
        if type(selected) is not list:
            raise ValueError("scenario catalog selection must be a list")
        baseline = {entry[schema.identity]: entry for entry in clean[name]} if schema.identity else {}
        seen = set()
        for entry in selected:
            if type(entry) is not dict or schema.identity not in entry:
                raise ValueError("scenario catalog entry lacks its identity")
            identity = entry[schema.identity]
            if type(identity) is not int or identity not in baseline or identity in seen:
                raise ValueError("scenario selected an unknown or duplicate catalog identity")
            seen.add(identity)
            if any(key not in baseline[identity] or type(value) is not type(baseline[identity][key])
                   or value != baseline[identity][key] for key, value in entry.items()):
                raise ValueError("scenario configuration disagrees with captured base catalog")
    return deepcopy(clean)
