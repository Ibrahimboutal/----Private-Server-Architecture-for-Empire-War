"""Startup compatibility without captured identities, rewards or payment offers."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest

from empire_war_private_server.auth.session_manager import Session
from empire_war_private_server.economy.resource_engine import EconomyError
from empire_war_private_server.handlers.client_startup import dispatch_client_startup


SESSION = Session("synthetic-session", 17, 23, float("inf"))
PHONE = {"type": "User", "action": "setUserPhoneInfo", "country": "local",
         "phoneModel": "synthetic emulator", "phoneOs": "android"}
UPGRADE = {"type": "Event", "action": "getApkUpgradeReward", "deviceType": "android",
           "packageChannel": "android", "version": 11520}
PUSH = {"type": "Event", "action": "saveGcmReg", "regId": "synthetic-push-token",
        "gcmPackageName": "local.synthetic.test"}
REQUESTS = [
    PHONE, {"type": "Mail", "action": "getUnreadCount"}, UPGRADE,
    {"type": "Event", "action": "getLoginShowInfo"},
    {"type": "Shop", "action": "getProductKeyList"},
    {"type": "Event", "action": "getQuestEvent"},
    {"type": "User", "action": "getPayInfo"},
    PUSH,
]


def _shape(value: object) -> object:
    if isinstance(value, dict):
        return {key: _shape(item) for key, item in value.items()}
    if isinstance(value, list):
        return "list"
    return type(value).__name__


@pytest.mark.parametrize("packet", REQUESTS, ids=lambda r: r["action"])
def test_each_startup_request_returns_independent_synthetic_response(packet: dict) -> None:
    before = deepcopy(packet)
    response = dispatch_client_startup(packet, SESSION)
    assert response is not None and response["ret"] == 0
    assert (response["type"], response["action"]) == (packet["type"], packet["action"])
    assert packet == before
    original = deepcopy(response)
    response.clear()
    assert dispatch_client_startup(packet, SESSION) == original


def test_phone_metadata_is_acknowledged_without_echoing_or_retaining_it() -> None:
    packet = {**PHONE, "phoneModel": "unique-device-marker", "country": "unique-country-marker"}
    assert dispatch_client_startup(packet, SESSION) == {
        "type": "User", "action": "setUserPhoneInfo", "ret": 0,
    }
    for startup in REQUESTS:
        serialized = json.dumps(dispatch_client_startup(startup, SESSION))
        assert "unique-device-marker" not in serialized
        assert "unique-country-marker" not in serialized


def test_push_token_is_discarded_and_namespace_is_exact() -> None:
    request = {**PUSH, "regId": "unique-token-marker", "gcmPackageName": "unique-package-marker"}
    assert dispatch_client_startup(request, SESSION) == {
        "type": "Event", "action": "saveGcmReg", "ret": 0,
    }
    assert dispatch_client_startup({**request, "type": "User"}, SESSION) is None
    for startup in REQUESTS:
        serialized = json.dumps(dispatch_client_startup(startup, SESSION))
        assert "unique-token-marker" not in serialized
        assert "unique-package-marker" not in serialized


@pytest.mark.parametrize("field,value", [
    ("regId", None), ("regId", []), ("regId", "x" * 4097),
    ("gcmPackageName", None), ("gcmPackageName", True), ("gcmPackageName", "x" * 257),
])
def test_push_fields_are_bounded_without_echoing_tokens(field: str, value: object) -> None:
    with pytest.raises(EconomyError) as exc:
        dispatch_client_startup({**PUSH, field: value}, SESSION)
    assert exc.value.ret == 900103
    assert str(exc.value) == f"Invalid {field}"


@pytest.mark.parametrize("field,value", [
    ("country", None), ("phoneModel", 5), ("phoneOs", []),
    ("country", "x" * 129), ("phoneModel", "x" * 257), ("phoneOs", "x" * 33),
])
def test_phone_fields_are_bounded_and_errors_do_not_echo_values(field: str, value: object) -> None:
    with pytest.raises(EconomyError) as exc:
        dispatch_client_startup({**PHONE, field: value}, SESSION)
    assert exc.value.ret == 900103
    assert str(exc.value) == f"Invalid {field}"


@pytest.mark.parametrize("field,value,code", [
    ("version", True, 900103), ("version", 11520.0, 900103),
    ("version", -1, 900103), ("version", 2_147_483_648, 900103),
    ("deviceType", None, 900103), ("packageChannel", [], 900103),
    ("version", 11521, 900105), ("packageChannel", "FT", 900105),
    ("deviceType", "ios", 900105),
])
def test_upgrade_acknowledgement_is_limited_to_observed_profile(field: str, value: object, code: int) -> None:
    with pytest.raises(EconomyError) as exc:
        dispatch_client_startup({**UPGRADE, field: value}, SESSION)
    assert exc.value.ret == code


@pytest.mark.parametrize("city_id", [1, True, "23", None])
@pytest.mark.parametrize("packet", REQUESTS, ids=lambda r: r["action"])
def test_extra_city_context_cannot_select_another_city(packet: dict, city_id: object) -> None:
    with pytest.raises(EconomyError) as exc:
        dispatch_client_startup({**packet, "cityId": city_id}, SESSION)
    assert exc.value.ret == 900102


def test_login_ui_and_store_do_not_advertise_claims_or_real_products() -> None:
    info = dispatch_client_startup({"type": "Event", "action": "getLoginShowInfo"}, SESSION)
    assert info is not None
    assert info["newComerSignInfo"]["uid"] == SESSION.user_id
    for name in ("couldGetNobilityReward", "turnPlayFree", "oneYearEventIsShow", "recallIconShow",
                 "hasPay", "monthCardOnSale", "honorMonthCardOnSale", "goldShopIcon", "giftCodeSwitch"):
        assert not info[name]
    assert info["eventTimeList"] == info["discountGidList"] == []
    assert "heroSubscribe" not in info
    store = dispatch_client_startup({"type": "User", "action": "getPayInfo"}, SESSION)
    assert store is not None and store["moneyShopList"] == []
    assert store["payssionCountry"] == "" and store["alipay"] == 0
    assert all(not item for item in store["nextReward"].values())
    assert dispatch_client_startup({"type": "Shop", "action": "getProductKeyList"}, SESSION)["keyList"] == []
    assert dispatch_client_startup({"type": "Event", "action": "getOnlineReward"}, SESSION) is None


@pytest.mark.parametrize("packet", [
    {"type": "User", "action": "getLoginShowInfo"},
    {"type": "Event", "action": "getPayInfo"},
    {"type": [], "action": "getPayInfo"}, {"type": "User", "action": {}}, {},
])
def test_unknown_or_malformed_routes_fall_through(packet: dict) -> None:
    assert dispatch_client_startup(packet, SESSION) is None


def test_response_shapes_match_read_only_capture_evidence() -> None:
    capture = Path(__file__).resolve().parents[1] / "new-set-up/game_protocol_v2.db"
    if not capture.exists():
        pytest.skip("local evidence database is not packaged")
    # Row 24856 establishes the login-show shape without heroSubscribe.
    sources = (6, 11, 13, 24856, 20, 19, 23, 3026)
    with sqlite3.connect(capture.resolve().as_uri() + "?mode=ro", uri=True) as db:
        for packet, row_id in zip(REQUESTS, sources, strict=True):
            raw = db.execute("SELECT payload_json FROM captured_packets WHERE id=?", (row_id,)).fetchone()[0]
            observed = json.loads(raw)
            local = dispatch_client_startup(packet, SESSION)
            assert _shape(local) == _shape(observed)
            if row_id in (6, 13, 19, 3026):
                assert local == observed
        profiles = {tuple(json.loads(raw)[field] for field in ("deviceType", "packageChannel", "version"))
                    for (raw,) in db.execute("SELECT payload_json FROM captured_packets "
                                            "WHERE action_name='getApkUpgradeReward' "
                                            "AND direction='CLIENT -> SERVER'")}
        assert profiles == {("android", "android", 11520)}
        push_responses = [json.loads(raw) for (raw,) in db.execute(
            "SELECT payload_json FROM captured_packets WHERE action_name='saveGcmReg' "
            "AND direction='SERVER -> CLIENT'")]
        assert len(push_responses) == 4
        assert all(response == dispatch_client_startup(PUSH, SESSION) for response in push_responses)
