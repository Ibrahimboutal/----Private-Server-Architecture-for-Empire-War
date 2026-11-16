"""Safe read-only compatibility views for the synthetic account."""

from __future__ import annotations

from typing import Any

from ..auth.session_manager import Session
from ..economy.gameplay_engine import GameplayEngine
from ..economy.resource_engine import ResourceEngine
from ..protocol.city_position import owned_city


ROUTES = frozenset({
    ("Building", "allValid"),
    ("Chat", "getForbidInfo"),
    ("City", "getCityList"),
    ("City", "getCityProduct"),
    ("City", "getMilitaryOfCity"),
    ("City", "getCitySoldier"),
    ("City", "getCitySoldierInfo"),
    ("City", "getLevyInfo"),
    ("City", "getPacifyPeopleInfo"),
    ("Event", "getEventList"),
    ("Event", "getGameNews"),
    ("Event", "getOneYearEventList"),
    ("Event", "getSubscribeHero"),
    ("Goods", "getSingleGoods"),
    ("Goods", "getMojiaGoods"),
    ("Goods", "getRedPacketGoods"),
    ("Goods", "getResourcePackList"),
    ("Goods", "getXunlianshiGoods"),
    ("GroupChat", "getMyRoomList"),
    ("Mail", "getInboxMail"),
    ("Market", "getUserBuyList"),
    ("NationalWar", "getNationalWarInfo"),
    ("Office", "getHeroDetail"),
    ("Office", "getCallbackHeroList"),
    ("Office", "getHeroListWithLargessTime"),
    ("Buffer", "getHeroBuffer"),
    ("Rank", "getPageRank"),
    ("Report", "getReport"),
    ("School", "getStartTroopInfo"),
    ("Sign", "getSignInfo"),
    ("Task", "getTaskDetail"),
    ("Task", "getTaskInfoList"),
    ("User", "getNobilityInfo"),
    ("User", "loadHeroDetail"),
    ("User", "loadHerosDetail"),
    ("User", "loadUserGoodsByType"),
    ("Hero", "getUserTalentBookList"),
    ("Armor", "getUserArmorList"),
    ("Armor", "getUserHorseList"),
    ("Ground", "getStartTroopInfoByXY"),
    ("Ground", "getWoundedSoldier"),
    ("Shop", "getShopInfoByGids"),
    ("Shop", "getMonthCardInfo"),
    ("Shop", "getShopInfoByType"),
    ("World", "getUnionPowers"),
    ("World", "getWorldMapOverview"),
    ("Trick", "getTrickList"),
    ("Union", "getUnionApplyInvite"),
    ("Union", "getRecommendUnionList"),
    ("Union", "getUnionHelpList"),
    ("Union", "loadUnionDetail"),
    ("Union", "loadUnionInfo"),
    ("Union", "loadUnionMemberList"),
})


def _city_list(session: Session) -> dict[str, Any]:
    return {
        "citys": [{
            "attacked_endtime": 0,
            "chiefhid": 0,
            "chiefname": "",
            "cid": session.wire_city_id,
            "complaint": 0,
            "govermentLevel": 1,
            "morale": 100,
            "moveCityEndTime": 0,
            "name": "Research City",
            "people": 0,
            "peopleMax": 0,
            "province": 0,
            "state": 0,
            "taketime": 0,
            "type": 0,
            "uid": session.user_id,
        }]
    }


async def dispatch_compat_views(request: dict[str, Any], session: Session,
                                gameplay: GameplayEngine | None = None,
                                engine: ResourceEngine | None = None) -> dict[str, Any] | None:
    """Return bounded informational replies without enabling mutations."""
    kind, action = request.get("type"), request.get("action")
    if not isinstance(kind, str) or not isinstance(action, str) or (kind, action) not in ROUTES:
        return None
    if "cityId" in request or (kind, action) in {
        ("City", "getCityProduct"),
        ("City", "getMilitaryOfCity"), ("City", "getCitySoldier"),
        ("City", "getCitySoldierInfo"), ("City", "getLevyInfo"),
        ("City", "getPacifyPeopleInfo"), ("Building", "allValid"),
        ("School", "getStartTroopInfo"),
    }:
        owned_city(request, session)

    payload: dict[str, Any]
    if action == "allValid":
        buildings = []
        if gameplay is not None:
            snapshot = await gameplay.snapshot(session.user_id, session.city_id)
            buildings = [
                {"bid": item["bid"], "level": item["level"],
                 "canUpgrade": int((item["bid"], item["level"] + 1) in gameplay._layout_upgrades
                                  or (item["bid"], item["level"] + 1) in gameplay._buildings),
                 "conditions": []}
                for item in snapshot["buildings"]
            ]
        payload = {"buildings": buildings}
    elif action == "getCityList":
        payload = _city_list(session)
    elif action == "getCityProduct":
        payload = {"productInfo": {}}
    elif action == "getMilitaryOfCity":
        payload = {
            "cityTroops": 0, "defenceList": [], "defenceLockList": [],
            "defenceTroops": 0, "myOutTroopList": [], "myOutTroops": 0,
            "soldierList": [], "soldierLockList": [], "unionStayTroopList": [],
            "unionStayTroops": 0, "woundedTroops": 0,
        }
    elif action == "getCitySoldierInfo":
        payload = {"defenceList": [], "soldierList": []}
    elif action == "getCitySoldier":
        payload = {"soliderList": []}
    elif action == "getWoundedSoldier":
        payload = {"soldiers": []}
    elif action == "getStartTroopInfoByXY":
        payload = {"gridLevel": 0, "gridType": 0, "hasMonster": False}
    elif action == "getStartTroopInfo":
        city_res: dict[str, Any] = {}
        if gameplay is not None:
            city_res = (await gameplay.snapshot(session.user_id, session.city_id))["resources"]
        elif engine is not None:
            city_res = (await engine.get_resources(session.user_id, session.city_id)).to_wire()
        payload = {
            "attackSpeedList": [], "bufferList": [], "cityRes": city_res,
            "dakBuildLevel": 0, "goodsList": [], "groundBuildLevel": 0,
            "groundLevelLimit": 0, "heroList": [], "soldierList": [],
            "technics": [],
        }
    elif action == "getWorldMapOverview":
        payload = {"capitalCityList": [], "myCityList": []}
    elif action == "getUnionPowers":
        payload = {
            "cid2UnionTag": "", "enemyUnionPowerList": "",
            "friendUnionPowerList": "", "myUnionPowerList": "",
            "noramlUnionPowerList": "", "npcPowerList": "",
        }
    elif action in ("getHeroDetail", "loadHeroDetail"):
        payload = {"hero": {}}
        if action == "loadHeroDetail":
            payload["userName"] = ""
    elif action == "loadHerosDetail":
        payload = {"heroes": []}
    elif action == "getNobilityInfo":
        empty_nobility = {"cityCount": 0, "id": 0, "name": "", "salary": 0, "tid": 0}
        payload = {
            "couldGetReward": False, "kingRewardList": [], "nextNobility": empty_nobility.copy(),
            "nextRewardList": [], "nobilityInfo": empty_nobility.copy(),
            "realNobilityInfo": empty_nobility.copy(), "requireList": [], "rewardList": [],
        }
    elif action in ("getSingleGoods", "getShopInfoByGids"):
        payload = {"goods": {}} if action == "getSingleGoods" else {"goodsList": []}
    elif action == "getShopInfoByType":
        payload = {"goodsList": [], "vipShopList": []}
    elif action == "loadUserGoodsByType":
        payload = {"goodList": []}
    elif action in ("getUserArmorList", "getUserHorseList"):
        payload = {"armorList": []}
    elif action == "getUserTalentBookList":
        payload = {"advTalentStuff": 0, "buffBookLimit": 0, "talentStuff": 0, "userBookList": []}
    elif action == "getPageRank":
        payload = {"page": 0, "rankList": [], "rankType": "user", "refreshTime": 0, "totalPage": 0}
    elif action == "getTaskInfoList":
        payload = {"taskInfos": []}
    elif action == "getTaskDetail":
        payload = {"taskDetail": {"cfgTask": {}, "goalList": [], "rewardList": []}}
    elif action == "getPacifyPeopleInfo":
        payload = {"pacifyInfo": {"jtFood": 0, "jtGold": 0, "lastAction": 0,
                                  "lastTime": 0, "qfGold": 0, "zdFood": 0,
                                  "zdPeople": 0, "zzFood": 0}}
    elif action == "getLevyInfo":
        payload = {"levyInfo": {"food": 0, "gold": 0, "iron": 0, "lastTime": 0,
                                "morale": 0, "rock": 0, "wood": 0}}
    elif action == "getHeroListWithLargessTime":
        payload = {"heroList": []}
    elif action == "getSignInfo":
        payload = {
            "countRewardList": [], "gameTime": "",
            "userSignInfo": {"maxSignCount": 0, "signCount": 0,
                             "todayIsSign": False, "uid": session.user_id},
        }
    elif action == "getReport":
        payload = {"lastItemIndex": 0, "reports": [], "totalCount": 0}
    elif action == "getInboxMail":
        payload = {"lastItemIndex": 0, "mailList": "[]", "totalCount": 0}
    elif action == "getEventList":
        payload = {"eventList": []}
    elif action == "getGameNews":
        payload = {"newsList": [], "newsType": 0, "serverTime": 0}
    elif action == "getOneYearEventList":
        payload = {"beerExchangeCount": 0, "beerExchangeTicketCount": 0,
                   "eventList": [], "oneValue": "0", "ticketCount": 0,
                   "yearValue": "0"}
    elif action == "getSubscribeHero":
        payload = {"heroList": [], "heroSubscribe": {}}
    elif action == "getResourcePackList":
        payload = {"goodsList": [], "shopList": []}
    elif action == "getRedPacketGoods":
        payload = {"goodsList": []}
    elif action in ("getMojiaGoods", "getXunlianshiGoods"):
        payload = {"goodsList": []}
    elif action == "getMyRoomList":
        payload = {"maxMember": 20, "roomList": []}
    elif action == "getNationalWarInfo":
        payload = {"description": "", "endtime": 0, "hasJoin": 0,
                   "logSize": 0, "starttime": 0, "supportCid": 0}
    elif action == "getCallbackHeroList":
        payload = {}
    elif action == "getHeroBuffer":
        payload = {"bufferList": [], "heroOtherInfo": {}, "nowTime": 0}
    elif action == "getUserBuyList":
        payload = {"lastItemIndex": 0, "totalCount": 0, "tradeList": []}
    elif action == "getTrickList":
        payload = {"trickList": [], "trickPoint": {}}
    elif action == "getMonthCardInfo":
        payload = {"cardInfo": {}, "leftDays": 0, "productId": "",
                   "todayGetReward": False}
    elif action == "getUnionApplyInvite":
        payload = {"applys": [], "invites": []}
    elif action == "getRecommendUnionList":
        payload = {"unionList": []}
    elif action == "getUnionHelpList":
        payload = {"helpList": [], "maxScore": 0, "myTodayScore": 0,
                   "myTotalScore": 0}
    elif action == "loadUnionMemberList":
        payload = {"memberList": []}
    elif action in ("loadUnionInfo", "loadUnionDetail"):
        payload = {"union": {}}
    else:
        payload = {}
    return {"type": kind, "action": action, "ret": 0, **payload}
