"""Read captures without modification; export only static costs and empty shapes.

Run from repository root. No captured credentials, IDs, chat or user balances
are copied. Schema/shape comes from a successful login; seed values are generated
synthetic data. The source database must be supplied explicitly.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sqlite3

import yaml

ROOT = Path(__file__).resolve().parents[1]
COST_KEYS = {"food": "foodNeed", "wood": "woodNeed", "iron": "ironNeed",
             "rock": "rockNeed", "gold": "goldNeed", "people": "peopleNeed"}


def empty_shape(value: object) -> object:
    """Keep object keys and scalar types, discarding all captured leaf values."""
    if isinstance(value, dict):
        return {key: empty_shape(item) for key, item in value.items()}
    if isinstance(value, list):
        return []
    if isinstance(value, bool):
        return False
    if isinstance(value, str):
        return ""
    if isinstance(value, float):
        return 0.0
    if isinstance(value, int):
        return 0
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    variants: dict[int, set[tuple[int, ...]]] = defaultdict(set)
    source_rows: list[int] = []
    template: dict | None = None
    with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.execute("BEGIN")
        for row_id, raw in db.execute("SELECT id,payload_json FROM captured_packets WHERE action_name='doLogin' AND direction='SERVER -> CLIENT' AND payload_json IS NOT NULL ORDER BY id"):
            packet = json.loads(raw)
            if packet.get("ret") != 0 or "cfgSoldier" not in packet:
                continue
            if template is None:
                template = empty_shape(packet)
            source_rows.append(row_id)
            for soldier in packet["cfgSoldier"]:
                values = tuple(soldier[key] for key in COST_KEYS.values())
                if any(type(value) is not int or value < 0 for value in values):
                    raise ValueError("capture contains invalid troop base cost")
                variants[soldier["sid"]].add(values)
    if template is None or not variants or any(len(v) != 1 for v in variants.values()):
        raise ValueError("missing or conflicting captured troop cost evidence")
    troop_data = {
        "source": {"database": args.database.name, "action": "doLogin.cfgSoldier",
                   "row_ids": source_rows, "semantics": "base costs; server modifiers unverified"},
        "troops": {sid: dict(zip(COST_KEYS, next(iter(values)))) for sid, values in sorted(variants.items())},
    }
    output = ROOT / "empire_war_private_server"
    (output / "data").mkdir(exist_ok=True)
    (output / "economy/troop_config.yaml").write_text(yaml.safe_dump(troop_data, sort_keys=False), encoding="utf-8")
    (output / "data/login_shape.json").write_text(json.dumps(template, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    manifest = {
        "source_database": args.database.name, "login_row_ids": source_rows,
        "troop_count": len(variants), "captured_personal_values_exported": False,
        "login_shape_sha256": hashlib.sha256(json.dumps(template, sort_keys=True).encode()).hexdigest(),
        "notes": ["Login shape fields are zero/empty placeholders, not claimed gameplay defaults.",
                  "Costs copied only from explicit cfgSoldier numeric fields; not inferred from resource deltas."],
    }
    (output / "data/evidence_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Exported {len(variants)} consistent troop base cost entries; no captured user values.")


if __name__ == "__main__":
    main()
