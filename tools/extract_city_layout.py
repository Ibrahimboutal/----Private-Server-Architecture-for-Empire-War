"""Extract ten occupied plots and their reviewed level-one inspection panels."""

import argparse
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
# (login row containing the plot, matching getBuildingInfo response)
SOURCES = (
    (4, 2778), (4, 2765), (4, 2740), (4, 2753), (4, 8046), (4, 15027),
    (4, 2149), (14828, 15469), (14828, 14694), (14828, 17302),
)
QUOTE_FIELDS = ("bid", "description", "foodNeed", "goldNeed", "ironNeed",
                "level", "name", "peopleNeed", "rockNeed", "summary", "upgradeTime", "woodNeed")


def extract(database: Path) -> dict:
    """Retain only non-account fields selected from matching successful replies."""
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.execute("BEGIN")

        def packet(row: int, action: str) -> dict:
            result = db.execute("SELECT payload_json FROM captured_packets WHERE id=? "
                                "AND action_name=? AND direction='SERVER -> CLIENT'",
                                (row, action)).fetchone()
            if result is None:
                raise ValueError(f"missing evidence row {row}")
            value = json.loads(result[0])
            if value.get("ret") != 0 or value.get("action") != action:
                raise ValueError(f"unexpected envelope at row {row}")
            return value

        result = []
        for login_row, info_row in SOURCES:
            login, info = packet(login_row, "doLogin"), packet(info_row, "getBuildingInfo")
            cfg = {item["name"]: item for item in login["cfgBuilding"]}
            building = info["building"]
            bid, level, xy = (building[field] for field in ("bid", "level", "xy"))
            if level != 1 or not any((b["bid"], b["level"], b["xy"], b["state"]) == (bid, level, xy, 0)
                                     for b in login["sysBuildings"]):
                raise ValueError(f"building at {info_row} does not match layout at {login_row}")
            view = {}
            for label, target in (("currBuilding", level), ("nextBuilding", level + 1)):
                quote = info[label]
                if (quote["bid"], quote["level"]) != (bid, target):
                    raise ValueError(f"inconsistent {label} at {info_row}")
                equipment = {key: quote[key] for key in QUOTE_FIELDS}
                conditions = []
                for condition in quote["conditions"]:
                    if condition["type"] != 0 or condition["collegeBuilding"]:
                        raise ValueError("selected fixture supports building prerequisites only")
                    requirement = condition["upgradeNeedName"]
                    conditions.append({"type": 0, "collegeBuilding": False, "required_bid": cfg[requirement]["bid"],
                                       "upgradeNeedName": requirement, "upgradeNeedLevel": condition["upgradeNeedLevel"]})
                view[label] = dict(equipment, conditions=conditions)
            if bid == 10:
                # Only the static Tavern refresh interval is selected.
                view["freshUnitTime"] = info["freshUnitTime"]
            result.append({"bid": bid, "level": level, "xy": xy, "inner": cfg[view["currBuilding"]["name"]]["inner"],
                           "layout_row": login_row, "detail_row": info_row, "view": view})
    return {"version": 1, "buildings": result}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    payload = extract(parser.parse_args().database)
    target = ROOT / "empire_war_private_server/data/city_layout.json"
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
