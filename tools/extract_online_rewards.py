"""Extract reviewed, account-free online reward definitions from local captures."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
GOODS_FIELDS = (
    "artifact", "canOpen", "count", "description", "directUse", "gid", "name",
    "position", "redGid", "resourceType", "special", "subType", "subTypeDes", "type", "value",
)


def extract(database: Path) -> dict:
    """Select the invariant catalog, not any account's progress or inventory."""
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        rows = db.execute("SELECT id,payload_json FROM captured_packets WHERE "
                          "action_name='getOnlineRewardInfo' AND direction='SERVER -> CLIENT'").fetchall()
        catalogs = [json.loads(raw)["onlineRewardInfoList"] for _, raw in rows
                    if json.loads(raw).get("ret") == 0]
        if not catalogs or any(item != catalogs[0] for item in catalogs):
            raise ValueError("reward catalog must agree across all successful captures")
        claims = [(row, json.loads(raw)) for row, raw in db.execute(
            "SELECT id,payload_json FROM captured_packets WHERE "
            "action_name='getOnlineReward' AND direction='SERVER -> CLIENT'")]
    stages = []
    for entry in catalogs[0]:
        def goods(item: dict) -> dict:
            return {**{key: item[key] for key in GOODS_FIELDS}, "cid": 0, "endtime": 0,
                    "state": 0, "stateEndtime": 0, "userName": ""}
        stages.append({"needOnlineTime": entry["needOnlineTime"],
                       "packageGoods": goods(entry["packageGoods"]),
                       "goodsList": [goods(item) for item in entry["goodsList"]],
                       "resList": [{"type": item["type"], "count": item["count"]}
                                   for item in entry["resList"]]})
    transitions = {}
    for row, claim in claims:
        step = claim.get("step")
        if claim.get("ret") == 0 and type(step) is int and 1 <= step < len(stages):
            if claim["timeLeft"] != stages[step]["needOnlineTime"]:
                raise ValueError(f"unreviewed cooldown at row {row}")
            transitions.setdefault(step - 1, row)
    if set(transitions) != set(range(6)) or len(stages) != 7:
        raise ValueError("expected six observed claim transitions and seven offers")
    return {"version": 1, "catalog_rows": [row for row, _ in rows],
            "claim_rows": {str(k): v for k, v in sorted(transitions.items())},
            "stages": stages}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    data = extract(parser.parse_args().database)
    target = ROOT / "empire_war_private_server/data/online_rewards.json"
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(data['stages'])} offers; {len(data['claim_rows'])} proven transitions")
