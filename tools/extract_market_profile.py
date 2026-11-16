"""Extract one sanitized market listing fixture from the read-only capture DB.

The capture establishes a one-food listing and its resource effect. It does
not establish a general server pricing formula or the cause of the reported
overflow. Never retain captured identities, location-derived distance or dates.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sqlite3
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REQUEST_FIELDS = ("resType", "resCount", "gold", "hour", "unionSel")
TRADE_FIELDS = (
    "buycid", "cid", "count", "distance", "endtime", "generateTime", "gold",
    "id", "limittime", "price", "restype", "state", "tooLong", "unionOnly", "unionid",
)
OWN_TRADE_FIELDS = tuple(key for key in TRADE_FIELDS if key != "tooLong") + (
    "originalEndtime", "starttime",
)
RESET_FIELDS = {"buycid", "cid", "id", "unionid", "generateTime", "distance"}
SELL_DATA_FIELDS = ("countLimit",) + tuple(
    resource + suffix for resource in ("food", "wood", "iron", "rock")
    for suffix in ("Price", "PriceMax", "PriceMin")
)


def extract(database: Path) -> dict[str, Any]:
    """Validate selected observations and return only non-private fixture data."""
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.execute("BEGIN")

        def packet(row_id: int, action: str, *, request: bool = False) -> dict[str, Any]:
            row = db.execute(
                "SELECT action_name,direction,payload_json FROM captured_packets WHERE id=?",
                (row_id,),
            ).fetchone()
            direction = "CLIENT -> SERVER" if request else "SERVER -> CLIENT"
            if row is None or row[:2] != (action, direction):
                raise ValueError(f"capture row {row_id} does not match selected evidence")
            payload = json.loads(row[2])
            if not request and payload.get("ret") != 0:
                raise ValueError("selected response is not successful")
            return payload

        request = packet(19126, "sellToUser", request=True)
        success = packet(19127, "sellToUser")
        owned = packet(19135, "doGetCityTrade")
        sell_data = packet(19112, "getUserSellData")["citySellData"]
        if any(p["type"] != "Market" for p in (request, success, owned)):
            raise ValueError("market namespace evidence changed")
        request_quote = {key: request[key] for key in REQUEST_FIELDS}
        if request_quote != {
            "resType": 0, "resCount": 1, "gold": 0.119999997317791,
            "hour": 12.0, "unionSel": 0,
        }:
            raise ValueError("selected one-food quote changed")
        trade = success["trade"]
        if set(trade) != set(TRADE_FIELDS) or len(owned["tradingList"]) != 1:
            raise ValueError("selected listing shape changed")
        own_trade = owned["tradingList"][0]
        if set(own_trade) != set(OWN_TRADE_FIELDS):
            raise ValueError("selected own-list shape changed")
        if trade["id"] != own_trade["id"] or trade["cid"] != request["cityId"]:
            raise ValueError("selected listing identity does not match its request")
        if any(own_trade[key] != trade[key] for key in TRADE_FIELDS if key != "tooLong"):
            raise ValueError("the listing changed between creation and own-list query")
        if any(trade[key] != 0 for key in ("buycid", "endtime", "state", "tooLong", "unionOnly")):
            raise ValueError("selected listing is not the captured idle public listing")
        if own_trade["originalEndtime"] != 0 or own_trade["starttime"] != 0:
            raise ValueError("unexpected listing timer state")
        if (trade["count"], trade["restype"], trade["gold"], trade["price"], trade["limittime"]) != (
            1, 0, request_quote["gold"], 0.12, 43200,
        ):
            raise ValueError("selected listing price or duration changed")

        # Compare equal-length adjacent windows. Passive changes are identical;
        # the window containing the listing has one additional food deducted.
        prior, before, after = [
            packet(row_id, "synchCityResource")["cityRes"]
            for row_id in (19104, 19119, 19132)
        ]
        if not (prior["cid"] == before["cid"] == after["cid"] == request["cityId"]):
            raise ValueError("resource evidence belongs to different cities")
        if before["lastupdate"] - prior["lastupdate"] != 20 or after["lastupdate"] - before["lastupdate"] != 20:
            raise ValueError("resource evidence no longer spans equal twenty-second windows")
        if set(prior) != set(before) or set(before) != set(after):
            raise ValueError("resource shapes disagree")
        for key in before:
            if key in ("cid", "lastupdate"):
                continue
            isolated_delta = (after[key] - before[key]) - (before[key] - prior[key])
            if not math.isclose(isolated_delta, -1.0 if key == "food" else 0.0,
                                rel_tol=0.0, abs_tol=1e-7):
                raise ValueError(f"isolated listing resource effect changed for {key}")
        if set(sell_data) != set(SELL_DATA_FIELDS) or sell_data["countLimit"] != 100000:
            raise ValueError("selected market data shape or count limit changed")

        def sanitized(source: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
            return {key: (0.0 if key == "distance" else 0) if key in RESET_FIELDS else source[key]
                    for key in keys}

        return {
            "version": 1,
            "profile_id": "captured-market-food-v1",
            "request": request_quote,
            "costs": {"food": 1},
            "trade_template": sanitized(trade, TRADE_FIELDS),
            "own_list_template": sanitized(own_trade, OWN_TRADE_FIELDS),
            "get_user_sell_data": {key: sell_data[key] for key in SELL_DATA_FIELDS},
            "source_note": (
                "Fixed one-food listing only. Gold is the total listing ask, produced by the "
                "client as float32(count * unit price). The user-specified hour * gold * "
                "resCount overflow experiment is a separate research calculation, not an "
                "established official economic formula. Identity, distance and absolute "
                "time fields are synthetic placeholders. No general fee or expiry policy inferred."
            ),
            "provenance": {
                "database": database.name,
                "request_row": 19126, "success_row": 19127,
                "own_list_row": 19135, "sell_data_row": 19112,
                "resource_rows": [19104, 19119, 19132],
                "resource_comparison_seconds": 20,
                "native_file": "GhidraProjects/game_logic_output.c",
                "native_lines": {
                    "request_encode": 542594, "response_decode": 542651,
                    "mem_sell_trade_decode": 649359, "client_total_ask": 948550,
                },
            },
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    profile = extract(args.database)
    output = ROOT / "empire_war_private_server/data/market_scenario.json"
    output.write_text(json.dumps(profile, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                      encoding="utf-8")
    print("Exported the captured one-food market fixture without account or location data.")


if __name__ == "__main__":
    main()
