"""Read-only structural comparisons to optional local packet-capture evidence."""

import json
from pathlib import Path
import sqlite3

import pytest

from empire_war_private_server.economy.resource_engine import CityResource, load_troop_costs

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def captures():
    path = ROOT / "new-set-up/game_protocol_v2.db"
    if not path.is_file():
        pytest.skip("optional local capture database unavailable")
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    db.execute("BEGIN")
    try:
        yield db
    finally:
        db.close()


def test_all_captured_soldier_base_costs_agree(captures):
    costs = load_troop_costs()
    compared = 0
    for (raw,) in captures.execute("SELECT payload_json FROM captured_packets WHERE action_name='doLogin' AND direction='SERVER -> CLIENT' AND payload_json IS NOT NULL"):
        for soldier in json.loads(raw).get("cfgSoldier", []):
            assert costs[soldier["sid"]] == {
                name: soldier[f"{name}Need"] for name in ("food", "wood", "iron", "rock", "gold", "people")}
            compared += 1
    assert compared >= 99


def test_resource_fields_and_action_envelopes_match_capture(captures):
    resources = CityResource().to_wire()
    for action, kind, field in (("doLogin", "User", "memCityResource"),
                                ("synchCityResource", "City", "cityRes")):
        checked = False
        for (raw,) in captures.execute("SELECT payload_json FROM captured_packets WHERE action_name=? AND direction='SERVER -> CLIENT' AND payload_json IS NOT NULL", (action,)):
            payload = json.loads(raw)
            if field not in payload:
                continue
            assert payload["type"] == kind
            assert set(payload[field]) == set(resources)
            assert payload["ret"] == 0
            checked = True
            break
        assert checked


def test_login_fixture_contains_no_captured_scalar_values():
    shape = json.loads((ROOT / "empire_war_private_server/data/login_shape.json").read_text())
    def check(value):
        if isinstance(value, dict):
            for item in value.values():
                check(item)
        elif isinstance(value, list):
            assert value == []
        else:
            assert value in (None, False, 0, 0.0, "")
    check(shape)


def test_phase2_quotes_match_selected_captures(captures):
    profile = json.loads((ROOT / "empire_war_private_server/data/phase2_scenario.json").read_text(encoding="utf-8"))
    def row(row_id):
        return json.loads(captures.execute("SELECT payload_json FROM captured_packets WHERE id=?", (row_id,)).fetchone()[0])
    draft = row(14481)
    for quote in profile["draft"]["army_entries"]:
        original = next(a for a in draft["armys"] if a["sid"] == quote["sid"])
        assert all(original[key] == value for key, value in quote.items())
    house = row(24882)
    for quote in profile["buildings"]:
        original = house["currBuilding" if quote["target_level"] == 1 else "nextBuilding"]
        assert all(original[key] == value for key, value in quote["wire_info"].items())
        assert quote["costs"] == {name: original[f"{name}Need"] for name in quote["costs"]}
    assert profile["draft"]["queue_limit"] == row(14485)["soldierQueue"][0]["queueLimit"]
    assert all(value in (0, 0.0, "", False, None) or value == [] for value in profile["draft"]["chief_hero_shape"].values())
    quarry = row(14611)
    for quote in profile["quarry"]["quotes"]:
        original = quarry["currBuilding" if quote["target_level"] == 1 else "nextBuilding"]
        assert all(original[key] == value for key, value in quote["wire_info"].items())
        assert quote["costs"] == {name: original[f"{name}Need"] for name in quote["costs"]}
    assert profile["quarry"]["goldCost"] == row(14617)["goldCost"]
    assert profile["quarry"]["missing_wood_units"] == quarry["nextBuilding"]["woodNeed"] - int(row(14607)["cityRes"]["wood"])
    for effects, old, new in ((profile["house_effects"], row(26551)["cityResource"], row(26554)["cityRes"]),
                              (profile["quarry"]["effects"], row(14617)["cityResource"], row(14620)["cityRes"])):
        assert effects == {key: new[key] - old[key] for key in effects}
    for preview, original in ((profile["house_level2_view"], row(26555)),
                              (profile["quarry"]["level2_view"], row(14621))):
        for field, values in preview.items():
            assert all(original[field][key] == value for key, value in values.items())


def test_market_fixture_shapes_and_isolated_debit_match_captures(captures):
    profile = json.loads((ROOT / "empire_war_private_server/data/market_scenario.json").read_text(encoding="utf-8"))
    def row(row_id):
        return json.loads(captures.execute("SELECT payload_json FROM captured_packets WHERE id=?", (row_id,)).fetchone()[0])
    request, response = row(19126), row(19127)
    assert profile["request"] == {key: request[key] for key in profile["request"]}
    assert set(profile["trade_template"]) == set(response["trade"])
    assert set(profile["own_list_template"]) == set(row(19135)["tradingList"][0])
    reset = {"id", "cid", "buycid", "generateTime", "unionid", "distance"}
    for template, original in ((profile["trade_template"], response["trade"]),
                                (profile["own_list_template"], row(19135)["tradingList"][0])):
        assert all(value == (0 if key in reset else original[key]) for key, value in template.items())
    assert profile["get_user_sell_data"] == row(19112)["citySellData"]
    a, b, c = [row(index)["cityRes"] for index in (19104, 19119, 19132)]
    assert b["lastupdate"] - a["lastupdate"] == c["lastupdate"] - b["lastupdate"] == 20
    for key in a:
        delta = (c[key] - b[key]) - (b[key] - a[key])
        assert delta == pytest.approx(-1 if key == "food" else 0, abs=1e-7)
    assert profile["costs"] == {"food": 1}
