"""Extract a fixed, unowned terrain sample and reviewed world-view field shapes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3


ROOT = Path(__file__).resolve().parents[1]
TILE_KEYS = {"level", "maxlevel", "ownercid", "province", "state", "type", "updatetime", "wid"}
CITY_KEYS = {"attackedShowEndTime", "cid", "cityLevel", "cityName", "flag", "prestige",
             "state", "type", "uid", "unionFlag", "unionId", "unionTag", "userFace", "userName"}


def extract(database: Path) -> dict:
    """Copy public numeric terrain fields, replacing all player identities."""
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.execute("BEGIN")

        def packet(row_id: int) -> dict:
            return json.loads(db.execute("SELECT payload_json FROM captured_packets WHERE id=?", (row_id,)).fetchone()[0])

        source = packet(24015)
        if (source.get("type"), source.get("action"), source.get("ret")) != ("World", "getMapRawBlockData", 0):
            raise ValueError("unexpected terrain source envelope")
        tiles = source["blocks"]
        if len(tiles) != 100:
            raise ValueError("expected a complete 10 by 10 block")
        start = tiles[0]["wid"]
        for offset, tile in enumerate(tiles):
            if (set(tile) != TILE_KEYS or any(type(v) is not int for v in tile.values())
                    or tile["wid"] != start + offset or start % 100 != 0
                    or tile["ownercid"] != 0 or tile["state"] != 0 or tile["updatetime"] != 0
                    or not 1 <= tile["type"] <= 7 or tile["level"] != tile["maxlevel"]):
                raise ValueError("terrain source contains unreviewed fields or owned state")
        terrain = [{key: tile[key] for key in sorted(TILE_KEYS - {"wid"})} for tile in tiles]
        city_tiles = [tile for tile in packet(87)["blocks"]
                      if tile["type"] == 0 and tile["level"] == tile["maxlevel"] == 1
                      and tile["state"] == tile["updatetime"] == 0 and tile["ownercid"] > 0]
        if not city_tiles or set(city_tiles[0]) != TILE_KEYS:
            raise ValueError("expected a level one city tile")
        city_tile = {key: city_tiles[0][key] for key in sorted(TILE_KEYS - {"wid", "ownercid"})}
        # Match the repeated terrain's province for a coherent synthetic region.
        city_tile["province"] = terrain[0]["province"]
        city_info = packet(92)["blocks"][0]
        if set(city_info) != CITY_KEYS:
            raise ValueError("unexpected map city fields")
        empty_city = {key: "" if type(value) is str else 0 for key, value in city_info.items()}
        empty_replies = {}
        for row_id, field in ((81, "fieldStateList"), (9419, "troops"), (78, "friendList")):
            reply = packet(row_id)
            if reply[field] != [] or reply["ret"] != 0:
                raise ValueError("expected an empty informational reply")
            empty_replies[reply["action"]] = reply
    return {
        "version": 1, "terrain": terrain, "city_tile": city_tile,
        "city_info_shape": empty_city, "empty_replies": empty_replies,
        "provenance": {
            "database": database.name, "terrain_row": 24015, "city_tile_row": 87,
            "city_info_shape_row": 92, "empty_response_rows": [81, 9419, 78],
            "native_tile_id_lines": [1908521, 1908523],
            "native_block_id_line": 1948523, "native_block_bounds_lines": [1949830, 1949860],
            "scope": "One captured neutral terrain block repeated over the synthetic map, one owned level-one city, no deployed armies or monsters.",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    result = extract(args.database)
    destination = ROOT / "empire_war_private_server/data/world_fixture.json"
    destination.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print("Exported one unowned terrain sample and sanitized city/empty-view shapes.")


if __name__ == "__main__":
    main()
