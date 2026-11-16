"""Export reviewed static client catalogs from a read-only packet database.

Only fields listed in client_catalog.CATALOG_SCHEMAS are copied. Original
accounts, resources, endpoints and credentials are not exported. Ten tables
agree across successful logins; the varying monster display table deliberately
uses the fixed row 4 snapshot rather than claiming a general selection formula.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from empire_war_private_server.economy.client_catalog import CATALOG_SCHEMAS, GAME_VERSION, validate_catalogs


def extract(database: Path) -> dict[str, Any]:
    """Validate every successful login without retaining its private fields."""
    selected: dict[str, Any] | None = None
    row_ids: list[int] = []
    variants: dict[str, dict[str, list[int]]] = {name: defaultdict(list) for name in CATALOG_SCHEMAS}
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.execute("BEGIN")
        for row_id, raw in db.execute(
                "SELECT id,payload_json FROM captured_packets WHERE action_name='doLogin' "
                "AND direction='SERVER -> CLIENT' ORDER BY id"):
            if raw is None:
                continue
            packet = json.loads(raw)
            if packet.get("ret") != 0:
                continue
            if packet.get("type") != "User" or type(packet.get("gameVersion")) is not int or packet["gameVersion"] != GAME_VERSION:
                raise ValueError("successful login has an unexpected namespace or version")
            catalogs = validate_catalogs({name: packet.get(name) for name in CATALOG_SCHEMAS})
            row_ids.append(row_id)
            if row_id == 4:
                selected = catalogs
            for name, entries in catalogs.items():
                variants[name][json.dumps(entries, sort_keys=True, ensure_ascii=True)].append(row_id)
    if selected is None or not row_ids:
        raise ValueError("the selected successful login row 4 is unavailable")
    for name, observed in variants.items():
        if name != "cfgMonsterInfoList" and len(observed) != 1:
            raise ValueError(f"previously static client table {name} varies across captures")
    monster_variants = variants["cfgMonsterInfoList"]
    selected_monsters = json.dumps(selected["cfgMonsterInfoList"], sort_keys=True, ensure_ascii=True)
    return {
        "version": 1, "game_version": GAME_VERSION, "catalogs": selected,
        "provenance": {
            "database": database.name, "selected_login_row": 4,
            "successful_login_rows": row_ids,
            "catalog_variant_counts": {name: len(observed) for name, observed in variants.items()},
            "monster_display_fixture": {
                "selected_row": 4, "matching_rows": monster_variants[selected_monsters],
                "note": "Fixed row 4 monster display catalog; other captured monster variants are not selected dynamically.",
            },
            "native_file": "GhidraProjects/game_logic_output.c",
            "native_login_refresh_lines": [253901, 253905],
            "note": "Static display/base configuration only; does not enable additional server-authoritative mutations.",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    fixture = extract(args.database)
    destination = ROOT / "empire_war_private_server/data/client_catalog.json"
    destination.write_text(json.dumps(fixture, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print("Exported eleven reviewed static catalog tables, without captured account state.")


if __name__ == "__main__":
    main()
