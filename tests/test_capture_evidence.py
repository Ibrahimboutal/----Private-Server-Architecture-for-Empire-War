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
