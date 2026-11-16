"""Reviewed static catalogs preserve native lookup data without account state."""

from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest

from empire_war_private_server.economy.client_catalog import (
    CATALOG_SCHEMAS, load_client_catalog, merge_scenario_catalog, validate_catalogs,
)
from empire_war_private_server.economy.gameplay_engine import GameplayEngine
from empire_war_private_server.economy.resource_engine import ResourceEngine, load_troop_costs
from tools.extract_client_catalog import extract

ROOT = Path(__file__).resolve().parents[1]


def test_complete_catalog_has_stable_identities_and_fresh_nested_copies():
    first = load_client_catalog()
    assert first["gameVersion"] == 1719
    assert {name: len(first[name]) for name in CATALOG_SCHEMAS} == {
        "cfgBarracksLimitList": 10, "cfgBuilding": 20, "cfgDefence": 5,
        "cfgHeroLevel": 100, "cfgMonsterInfoList": 20, "cfgSoldier": 99,
        "cfgTalentInfoList": 109, "cfgTechnic": 21, "cfgTrick": 28,
        "cfgUnionTechnic": 0, "cfgVipPrivilegeList": 210,
    }
    assert first["cfgBuilding"][0]["name"] == "Farm"
    assert first["cfgSoldier"][0]["name"] == "Peasant"
    assert first["cfgMonsterInfoList"][0]["name"] == "Harpy"
    first["cfgSoldier"][0]["name"] = "caller mutation"
    first["cfgBuilding"].clear()
    second = load_client_catalog()
    assert second["cfgSoldier"][0]["name"] == "Peasant"
    assert len(second["cfgBuilding"]) == 20


def test_display_soldier_costs_match_the_existing_authoritative_base_table():
    base = load_troop_costs()
    entries = load_client_catalog()["cfgSoldier"]
    assert {entry["sid"] for entry in entries} == set(base)
    for entry in entries:
        assert base[entry["sid"]] == {name: entry[name + "Need"] for name in base[entry["sid"]]}


@pytest.mark.parametrize("scenario_id", ["captured-phase2-v1", "captured-quarry-shortage-v1"])
def test_scenario_selection_preserves_full_catalog_and_does_not_enable_mutations(tmp_path, scenario_id):
    ledger = ResourceEngine(tmp_path / "unused-state.db", tmp_path / "unused-audit.db")
    game = GameplayEngine(ledger, scenario_id=scenario_id)
    catalog = load_client_catalog()
    before = deepcopy(catalog)
    supported_armies = set(game._armies)
    supported_buildings = set(game._buildings)
    combined = merge_scenario_catalog(catalog, game.login_configuration())
    assert combined == catalog == before
    assert len(combined["cfgSoldier"]) == 99 and len(combined["cfgBuilding"]) == 20
    assert set(game._armies) == supported_armies
    assert set(game._buildings) == supported_buildings
    combined["cfgSoldier"].clear()
    assert len(catalog["cfgSoldier"]) == 99


@pytest.mark.parametrize("mutation", ["extra_field", "duplicate_id", "bool_numeric", "url", "credential", "email"])
def test_unreviewed_catalog_content_is_rejected(mutation):
    tables = load_client_catalog()
    tables.pop("gameVersion")
    if mutation == "extra_field":
        tables["cfgBuilding"][0]["userName"] = "unreviewed account value"
    elif mutation == "duplicate_id":
        tables["cfgSoldier"][1]["sid"] = tables["cfgSoldier"][0]["sid"]
    elif mutation == "bool_numeric":
        tables["cfgSoldier"][0]["foodNeed"] = True
    else:
        tables["cfgBuilding"][0]["description"] = {
            "url": "https://example.invalid/endpoint", "credential": "password=do-not-retain",
            "email": "account@example.invalid",
        }[mutation]
    with pytest.raises(ValueError):
        validate_catalogs(tables)


@pytest.mark.parametrize("scenario", [
    {"cfgSoldier": [{"sid": 1, "foodNeed": 0}]},
    {"cfgSoldier": [{"sid": 999999}]},
    {"cfgSoldier": [{"sid": 1, "password": "should-not-copy"}]},
    {"user": {"uid": 999}},
])
def test_scenario_overlay_cannot_change_base_costs_or_add_private_state(scenario):
    catalog = load_client_catalog()
    before = deepcopy(catalog)
    with pytest.raises(ValueError):
        merge_scenario_catalog(catalog, scenario)
    assert catalog == before


def test_extractor_excludes_account_fields_and_opens_source_read_only(tmp_path):
    source = tmp_path / "capture.db"
    packet = {"type": "User", "action": "doLogin", "ret": 0, **load_client_catalog(),
              "password": "DO_NOT_EXPORT_PASSWORD", "passport": "DO_NOT_EXPORT_PASSPORT",
              "user": {"uid": 777, "name": "DO_NOT_EXPORT_NAME"},
              "helpUrl": "https://example.invalid/private"}
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE captured_packets(id INTEGER, action_name TEXT, direction TEXT, payload_json TEXT)")
        db.execute("INSERT INTO captured_packets VALUES (4,'doLogin','SERVER -> CLIENT',?)", (json.dumps(packet),))
    original = source.read_bytes()
    result = extract(source)
    assert source.read_bytes() == original
    encoded = json.dumps(result)
    assert "DO_NOT_EXPORT" not in encoded and "example.invalid" not in encoded
    assert set(result["catalogs"]) == set(CATALOG_SCHEMAS)
    assert result["provenance"]["selected_login_row"] == 4
    assert result["provenance"]["monster_display_fixture"]["selected_row"] == 4


def test_bundled_catalog_matches_reviewed_source_rows():
    source = ROOT / "new-set-up/game_protocol_v2.db"
    if not source.is_file():
        pytest.skip("optional local capture database unavailable")
    saved = json.loads((ROOT / "empire_war_private_server/data/client_catalog.json").read_text(encoding="utf-8"))
    assert extract(source) == saved
    assert len(saved["provenance"]["successful_login_rows"]) == 28
    assert saved["provenance"]["catalog_variant_counts"]["cfgMonsterInfoList"] == 5
    assert all(count == 1 for name, count in saved["provenance"]["catalog_variant_counts"].items()
               if name != "cfgMonsterInfoList")
