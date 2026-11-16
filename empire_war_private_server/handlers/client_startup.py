"""Authenticated, empty-account startup responses for the local client fixture.

Wire names and scalar types come from capture rows 5/6, 9/11, 10/13,
16/21, 17/20, 18/19, 22/23 and 3024/3026 in ``game_protocol_v2.db``. Empty mail,
event and store lists describe the synthetic account, not captured player state
or an inferred official reward formula. No purchase/claim operation is enabled.
Device metadata is validated then discarded, without persistence or echoing.
"""

from __future__ import annotations

from typing import Any

from ..auth.session_manager import Session
from ..economy.resource_engine import EconomyError
from ..protocol.city_position import owned_city


SUPPORTED_STARTUP_ACTIONS = frozenset({
    ("User", "setUserPhoneInfo"),
    ("Mail", "getUnreadCount"),
    ("Event", "getApkUpgradeReward"),
    ("Event", "getLoginShowInfo"),
    ("Shop", "getProductKeyList"),
    ("Event", "getQuestEvent"),
    ("User", "getPayInfo"),
    ("Event", "saveGcmReg"),
})


def _bounded_string(request: dict[str, Any], field: str, maximum: int) -> str:
    """Apply a local input bound; never include the supplied value in errors."""
    value = request.get(field)
    if not isinstance(value, str) or len(value) > maximum:
        raise EconomyError(900103, f"Invalid {field}")
    return value


def _login_show_info(user_id: int) -> dict[str, Any]:
    """Return an explicit synthetic profile with no active promotions or claims.

    The base object shape is captured in row 24856, where ``heroSubscribe``
    is absent. ``nationalWarShowInfo.state=-1`` is the captured inactive state.
    Completed sign/target flags suppress unavailable reward workflows; no reward
    has actually been credited. This is a fixed UI fixture, not a reward engine.
    """
    return {
        "couldGetNobilityReward": False,
        "discountGidList": [],
        "donationTaskCount": 0,
        "eventTimeList": [],
        "giftCodeSwitch": 0,
        "giftCount": 0,
        "goldShopExpireTime": 0,
        "goldShopIcon": 0,
        "hasOldHeroSale": 0,
        "hasOptionalHeroSale": 0,
        "hasPay": False,
        "haveBuySubscribeHero": 0,
        "helpQueueCount": 0,
        "honorMonthCardOnSale": 0,
        "isAllTargetFinish": True,
        "isBuyHonorMonthCard": False,
        "isBuyMonthCard": False,
        "isGetHonorMonthReward": False,
        "isGetMonthReward": False,
        "isSignToday": True,
        "isWar": False,
        "monthCardOnSale": 0,
        "nationalWarShowInfo": {
            "battleId": 0, "battleOperation": 0, "hasJoin": 0,
            "state": -1, "supportCid": 0,
        },
        "newComerSignInfo": {
            "maxSignCount": 0, "signCount": 0,
            "todayIsSign": True, "uid": user_id,
        },
        "nextOnlineRewardTime": 0,
        "oneYearEventIsShow": False,
        "oneYearEventType": 0,
        "onlineStep": 0,
        "onlineTimeLeft": 0,
        "recallIconShow": False,
        "totalUnionTroopCount": 0,
        "turnPlayFree": False,
        "unionInvitedCount": 0,
    }


def dispatch_client_startup(request: dict[str, Any], session: Session) -> dict[str, Any] | None:
    """Handle startup queries after the router validates the connection session.

    No database, external service, device identifier storage or reward mutation
    is involved. Return ``None`` for other actions so normal routing continues.
    APK upgrade acknowledgements support only the captured Android 11.520
    request profile; they do not grant money, items or another entitlement.
    """
    kind, action = request.get("type"), request.get("action")
    if (not isinstance(kind, str) or not isinstance(action, str)
            or (kind, action) not in SUPPORTED_STARTUP_ACTIONS):
        return None
    # Captured startup requests are account-scoped and omit cityId. If a client
    # supplies one, never silently accept a different city's context.
    if "cityId" in request:
        owned_city(request, session)

    payload: dict[str, Any]
    if action == "setUserPhoneInfo":
        for field, maximum in (("country", 128), ("phoneOs", 32), ("phoneModel", 256)):
            _bounded_string(request, field, maximum)
        payload = {}
    elif action == "saveGcmReg":
        # Native Event::SaveGcmRegRequest::encode at physical export lines
        # 539720/539725 emits these strings. Four captured replies contain only
        # the success envelope. Push registration is a deliberate local no-op;
        # neither this token nor a registration request leaves the handler.
        _bounded_string(request, "regId", 4096)
        _bounded_string(request, "gcmPackageName", 256)
        payload = {}
    elif action == "getUnreadCount":
        payload = {"unReadMailCount": 0, "unReadReportCount": 0}
    elif action == "getApkUpgradeReward":
        device = _bounded_string(request, "deviceType", 32)
        channel = _bounded_string(request, "packageChannel", 64)
        version = request.get("version")
        if type(version) is not int or not 0 <= version <= 2_147_483_647:
            raise EconomyError(900103, "Invalid version")
        if (device, channel, version) != ("android", "android", 11520):
            raise EconomyError(900105, "Unsupported APK upgrade request profile")
        payload = {}
    elif action == "getLoginShowInfo":
        payload = _login_show_info(session.user_id)
    elif action == "getProductKeyList":
        payload = {"keyList": []}
    elif action == "getQuestEvent":
        payload = {"questInfo": []}
    else:  # User/getPayInfo: an empty local store with all payment routes disabled.
        payload = {
            "alipay": 0,
            "moneyShopList": [],
            "nextReward": {
                "armorList": [], "armorStuffList": [], "currentMoney": 0,
                "goodsList": [], "heroList": [], "score": 0, "totalMoney": 0,
            },
            "payssionCountry": "",
        }
    return {"type": kind, "action": action, "ret": 0, **payload}
