"""Select public tutorial definitions; erase captured account progress."""

import argparse
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]


def extract(database: Path) -> dict:
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        raw = db.execute("SELECT payload_json FROM captured_packets WHERE id=22617 "
                         "AND action_name='getTargetList' AND direction='SERVER -> CLIENT'").fetchone()
        packet = json.loads(raw[0])
    if packet.get("ret") != 0:
        raise ValueError("expected successful target reply")
    targets = []
    for target in packet["targetList"]:
        targets.append({key: target[key] for key in (
            "description", "id", "targetCount", "targetId", "targetLevel", "targetType", "useWhichData")})
    return {"version": 1, "source_row": 22617, "targets": targets,
            "rewards": [{"res": {"count": entry["res"]["count"], "type": entry["res"]["type"]}}
                        for entry in packet["rewardList"]]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    data = extract(parser.parse_args().database)
    (ROOT / "empire_war_private_server/data/profile_views.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("Wrote tutorial definitions without account progress")
