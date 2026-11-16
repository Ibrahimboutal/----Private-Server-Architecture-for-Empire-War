"""Export explicit, nonpersonal quotes for a bounded Phase 2 test scenario.

This combines two *independent* capture snapshots as an approved synthetic lab
scenario. It does not derive a shared bonus formula or import a player's city.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
RESOURCES = ("food", "wood", "iron", "rock", "gold", "people")
BUILDING_FIELDS = ("bid", "level", "name", "description", "summary", "foodNeed",
                   "woodNeed", "ironNeed", "rockNeed", "goldNeed", "peopleNeed", "upgradeTime",
                   "canUpgrade", "conditions")
SOLDIER_CONFIG_FIELDS = (
    "sid", "name", "description", "ap", "dp", "hp", "range", "speed", "carry",
    "foodNeed", "woodNeed", "rockNeed", "ironNeed", "goldNeed", "peopleNeed",
    "timeNeed", "foodUse", "type", "fromcity",
)
BUILDING_CONFIG_FIELDS = ("bid", "name", "description", "summary", "inner", "tableName")
ARMY_FIELDS = ("sid", "sname", "ap", "carry", "description", "dp", "draftTime",
               "foodNeed", "foodUse", "goldNeed", "hp", "ironNeed", "peopleNeed",
               "range", "rockNeed", "speed", "woodNeed")


def empty_shape(value: object) -> object:
    if isinstance(value, dict):
        return {key: empty_shape(item) for key, item in value.items()}
    if isinstance(value, list):
        return []
    if isinstance(value, str):
        return ""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return type(value)(0)
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.execute("BEGIN")
        def packet(row_id: int, action: str) -> dict:
            row = db.execute("SELECT action_name,direction,payload_json FROM captured_packets WHERE id=?", (row_id,)).fetchone()
            if row is None or row[:2] != (action, "SERVER -> CLIENT"):
                raise ValueError(f"capture row {row_id} does not match the selected evidence")
            result = json.loads(row[2])
            if result.get("ret") != 0:
                raise ValueError("selected evidence is not a successful response")
            return result

        login = packet(4, "doLogin")
        draft = packet(14481, "startDraftQueue")
        pending = packet(14491, "startDraftQueue")
        state = packet(14485, "getBuildingState")
        house = packet(24882, "getBuildingInfo")
        upgrade = packet(24885, "upgrade")
        if len(pending["queues"]) != 2 or pending["queues"][1]["state"] != 0:
            raise ValueError("pending draft evidence changed")
        if state["soldierQueue"][0]["queueLimit"] != 2:
            raise ValueError("draft queue limit evidence changed")
        if upgrade["building"]["stateEndtime"] - upgrade["building"]["stateStarttime"] != 60:
            raise ValueError("selected House duration evidence changed")
        armies = []
        for army in draft["armys"]:
            if army["sid"] not in (1, 2):
                continue
            conditions = army["conditions"]
            if not army["canDraft"] or len(conditions) != 1 or conditions[0]["type"] != 0 or conditions[0]["upgradeNeedLevel"] != 1 or conditions[0]["upgradeNeedName"] != "Barracks":
                raise ValueError("selected troop prerequisite evidence changed")
            armies.append({key: army[key] for key in ARMY_FIELDS})
        if {army["sid"] for army in armies} != {1, 2}:
            raise ValueError("missing supported troop quote")
        def fields_of(obj: dict, keys: tuple[str, ...]) -> dict:
            return {key: obj[key] for key in keys}

        def building_quote(info: dict, source_row: int) -> dict:
            return {"bid": info["bid"], "target_level": info["level"],
                    "costs": {key: info[f"{key}Need"] for key in RESOURCES},
                    "duration_seconds": info["upgradeTime"], "conditions": info["conditions"],
                    "wire_info": fields_of(info, BUILDING_FIELDS), "source_row": source_row}

        infos = []
        for name in ("currBuilding", "nextBuilding"):
            info = house[name]
            if info["bid"] != 5 or info["conditions"]:
                raise ValueError("House profile has unexpected prerequisites")
            infos.append(building_quote(info, 24882))

        quarry = packet(14611, "getBuildingInfo")
        costs = building_quote(quarry["nextBuilding"], 14611)["costs"]
        before = packet(14607, "synchCityResource")["cityRes"]
        immediate = packet(14617, "upgradeImmediatelyByGoldWhenLackRes")
        after = immediate["cityResource"]
        missing_wood = costs["wood"] - int(before["wood"])
        if (missing_wood != 822 or after["wood"] != 0 or before["lastupdate"] != after["lastupdate"]
                or immediate["goldCost"] != 6 or quarry["nextBuilding"]["conditions"]
                or any(before[key] - after[key] != costs[key] for key in RESOURCES if key != "wood")):
            raise ValueError("the captured Quarry deficit or payment has changed")
        house_instant = packet(26551, "upgradeImmediatelyByGoldWhenLackRes")
        def effects(old: dict, new: dict, keys: tuple[str, ...]) -> dict[str, int | float]:
            return {key: new[key] - old[key] for key in keys}

        quarry_effects = effects(after, packet(14620, "synchCityResource")["cityRes"],
                                 ("peopleWorking", "rockAdd", "rockMax"))
        house_effects = effects(house_instant["cityResource"], packet(26554, "synchCityResource")["cityRes"],
                                ("peopleMax", "peopleStable"))
        if house_effects != {"peopleMax": 200, "peopleStable": 180} or quarry_effects != {"peopleWorking": 40, "rockAdd": 266, "rockMax": 20000}:
            raise ValueError("the observed level completion effects have changed")
        profile = {
            "version": 1, "profile_id": "captured-phase2-v1",
            "provenance": {"database": args.database.name, "login_config_row": 4,
                "draft_response_row": 14481, "draft_pending_row": 14491,
                "draft_limit_row": 14485, "house_quote_row": 24882,
                "house_upgrade_row": 24885, "house_completion_row": 24904,
                "house_immediate_rows": [26546, 26551, 26554, 26555],
                "quarry_shortage_rows": [14607, 14611, 14613, 14617, 14620, 14621],
                "note": "Independent captured quotes composed into a synthetic scenario; no general bonus formula inferred."},
            "draft": {"barracks_bid": 9, "barracks_level": 1, "max_count": 50,
                "queue_limit": 2, "army_entries": armies,
                "chief_hero_shape": empty_shape(draft["chiefHero"]),
                "current_building": {key: draft["currBuilding"][key] for key in BUILDING_FIELDS},
                "next_building": {key: draft["nextBuilding"][key] for key in BUILDING_FIELDS},
                "duration_rule": "trunc(float32(float32(count) * float32(draftTime)))"},
            "buildings": infos,
            "house_effects": house_effects,
            "house_level2_view": {key: fields_of(packet(26555, "getBuildingInfo")[key], BUILDING_FIELDS)
                                 for key in ("currBuilding", "nextBuilding")},
            "quarry": {
                "quotes": [building_quote(quarry[field], 14611) for field in ("currBuilding", "nextBuilding")],
                "missing_wood_units": missing_wood, "goldCost": immediate["goldCost"],
                "effects": quarry_effects,
                "level2_view": {key: fields_of(packet(14621, "getBuildingInfo")[key], BUILDING_FIELDS)
                                for key in ("currBuilding", "nextBuilding")},
            },
            "cfg_soldier": [fields_of(item, SOLDIER_CONFIG_FIELDS) for item in login["cfgSoldier"] if item["sid"] in (1, 2)],
            "cfg_barracks_limits": [fields_of(item, ("level", "soldierLimit")) for item in login["cfgBarracksLimitList"]],
            "cfg_building": [fields_of(item, BUILDING_CONFIG_FIELDS) for item in login["cfgBuilding"] if item["bid"] in (3, 5, 9)],
        }
    output = ROOT / "empire_war_private_server/data/phase2_scenario.json"
    output.write_text(json.dumps(profile, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print("Exported SID1/2, House and Quarry fixtures without captured account state.")


if __name__ == "__main__":
    main()
