"""Check native map packing, capture parity, and synthetic ownership."""

from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest

from empire_war_private_server.auth.session_manager import Session
from empire_war_private_server.economy.resource_engine import EconomyError
from empire_war_private_server.handlers.world_views import dispatch_world_views
from tools.extract_world_fixture import extract


ROOT = Path(__file__).resolve().parents[1]
SESSION = Session("local-map", 1, 1, 999999, 1001)


def exchange(action: str, **fields):
    return dispatch_world_views({"type": "World", "action": action, **fields}, SESSION)


def native_tile_id(wid: int) -> int:
    # GroundTile::getTileId, physical lines 1908521–1908523.
    return ((wid % 10000) // 100) * 10 + ((wid % 100) // 10 + (wid // 10000) * 10) * 1000 + wid % 10


def test_four_initial_blocks_place_exactly_one_owned_city_and_round_trip_native_ids():
    response = exchange("getMapRawBlockData", blockData=[0, 100, 1, 101])
    assert response["ret"] == 0 and response["monsters"] == []
    assert len(response["blocks"]) == 400
    owned = [tile for tile in response["blocks"] if tile["ownercid"]]
    assert len(owned) == 1
    assert owned[0]["type"] == 0 and owned[0]["wid"] == 11 and owned[0]["ownercid"] == 1001
    assert len({tile["wid"] for tile in response["blocks"]}) == 400
    for tile in response["blocks"]:
        cid = native_tile_id(tile["wid"])
        native_block = (cid % 1000) // 10 + (cid // 10000) * 100
        assert native_block in (0, 100, 1, 101)
        assert tile["wid"] // 100 == native_block
    assert len({tile["type"] for tile in response["blocks"]}) == 8
    city = exchange("getMapGridCityInfo", cids="1001")["blocks"][0]
    assert city["cid"] == owned[0]["ownercid"]
    assert city["cityName"] == "Research City" and city["userName"] == "Research User"
    assert city["uid"] == 1 and city["unionId"] == 0
    assert exchange("getMapGridCityInfo", cids="2002")["blocks"] == []


def test_map_query_repeat_does_not_modify_fixture_or_request():
    packet = {"type": "World", "action": "getMapRawBlockData", "blockData": [0]}
    before = deepcopy(packet)
    response = dispatch_world_views(packet, SESSION)
    snapshot = deepcopy(response)
    response["blocks"][0]["level"] = -7
    response["blocks"].clear()
    assert dispatch_world_views(packet, SESSION) == snapshot
    assert packet == before


@pytest.mark.parametrize("blocks", [[], [True], [-1], [51], [5100], [500000], ["0"], [0, 0], list(range(10)), None, "0"])
def test_invalid_map_blocks_fail_before_building_the_view(blocks):
    with pytest.raises(EconomyError) as error:
        exchange("getMapRawBlockData", blockData=blocks)
    assert error.value.ret == 900103


def test_native_last_map_block_and_response_size_bound():
    response = exchange("getMapRawBlockData", blockData=[5050])
    assert len(response["blocks"]) == 100
    assert native_tile_id(response["blocks"][0]["wid"]) == 500500
    assert max(tile["wid"] for tile in response["blocks"]) == 505099
    response = exchange("getMapRawBlockData", blockData=list(range(9)))
    assert len(response["blocks"]) == 900
    assert len(json.dumps(response).encode()) < 1_048_576


@pytest.mark.parametrize("cids", [None, [], "", "1", "1000", "1001,", "1001,,2002", "١٠٠١", "-1001", "1001 " ])
def test_malformed_city_queries_are_rejected(cids):
    with pytest.raises(EconomyError) as error:
        exchange("getMapGridCityInfo", cids=cids)
    assert error.value.ret == 900103


def test_empty_synthetic_military_fields_and_diplomatic_views():
    assert exchange("getMyFieldState", cityId=1001)["fieldStateList"] == []
    assert exchange("getTroopsInWorld", centerX=1, centerY=1, radius=5)["troops"] == []
    response = dispatch_world_views({"type": "Union", "action": "getMyUnionFriendRelation"}, SESSION)
    assert response == {"type": "Union", "action": "getMyUnionFriendRelation", "ret": 0, "friendList": []}
    for value in (1, True, "1001", None, 1002):
        with pytest.raises(EconomyError) as error:
            exchange("getMyFieldState", cityId=value)
        assert error.value.ret == 900102
    with pytest.raises(EconomyError):
        exchange("getMapRawBlockData", blockData=[0], cityId=2002)


@pytest.mark.parametrize("fields,code", [
    ({"radius": 6}, 900105), ({"radius": True}, 900103),
    ({"centerX": 0}, 900103), ({"centerY": 501}, 900103), ({"centerY": "1"}, 900103),
])
def test_military_query_bounds(fields, code):
    with pytest.raises(EconomyError) as error:
        exchange("getTroopsInWorld", **{"centerX": 1, "centerY": 1, "radius": 5, **fields})
    assert error.value.ret == code


def test_unpositioned_city_requires_the_existing_position_recovery():
    unpositioned = Session("old-local", 1, 1, 999999)
    with pytest.raises(EconomyError) as error:
        dispatch_world_views({"type": "World", "action": "getMapRawBlockData", "blockData": [0]}, unpositioned)
    assert error.value.ret == 900105
    for packet in ({"type": "World", "action": "march"}, {}, {"type": []}, {"type": "World", "action": {}}):
        assert dispatch_world_views(packet, SESSION) is None


def test_world_fixture_and_packing_match_read_only_capture_evidence():
    database = ROOT / "new-set-up/game_protocol_v2.db"
    if not database.is_file():
        pytest.skip("optional local capture evidence unavailable")
    bundled = json.loads((ROOT / "empire_war_private_server/data/world_fixture.json").read_text(encoding="utf-8"))
    assert bundled == extract(database)
    assert all(tile["ownercid"] == tile["updatetime"] == tile["state"] == 0 for tile in bundled["terrain"])
    owned_count = 0
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        for (raw,) in db.execute("SELECT payload_json FROM captured_packets WHERE action_name='getMapRawBlockData' AND direction='SERVER -> CLIENT'"):
            response = json.loads(raw)
            tiles = response["blocks"]
            assert len(tiles) == 100
            assert [tile["wid"] for tile in tiles] == list(range(tiles[0]["wid"], tiles[0]["wid"] + 100))
            for tile in tiles:
                if tile["type"] == 0 and tile["ownercid"] > 0:
                    assert native_tile_id(tile["wid"]) == tile["ownercid"]
                    owned_count += 1
        assert owned_count == 1042
        for action, row_id in (("getMyFieldState", 81), ("getTroopsInWorld", 9419), ("getMyUnionFriendRelation", 78)):
            raw = db.execute("SELECT payload_json FROM captured_packets WHERE id=?", (row_id,)).fetchone()[0]
            assert bundled["empty_replies"][action] == json.loads(raw)
