from empire_war_private_server.auth.session_manager import Session
from empire_war_private_server.handlers.compat_views import dispatch_compat_views


SESSION = Session("compat", 1, 1, 999999, 1001)


async def test_read_only_compatibility_views_return_synthetic_empty_shapes():
    for kind, action, expected in (
        ("City", "getCityList", "citys"),
        ("City", "getCityProduct", "productInfo"),
        ("City", "getMilitaryOfCity", "soldierList"),
        ("City", "getCitySoldier", "soliderList"),
        ("City", "getCitySoldierInfo", "soldierList"),
        ("City", "getLevyInfo", "levyInfo"),
        ("City", "getPacifyPeopleInfo", "pacifyInfo"),
        ("Event", "getEventList", "eventList"),
        ("Event", "getGameNews", "newsList"),
        ("Event", "getOneYearEventList", "eventList"),
        ("Event", "getSubscribeHero", "heroList"),
        ("Goods", "getSingleGoods", "goods"),
        ("Goods", "getMojiaGoods", "goodsList"),
        ("Goods", "getResourcePackList", "goodsList"),
        ("Goods", "getRedPacketGoods", "goodsList"),
        ("Goods", "getXunlianshiGoods", "goodsList"),
        ("GroupChat", "getMyRoomList", "roomList"),
        ("Mail", "getInboxMail", "mailList"),
        ("Market", "getUserBuyList", "tradeList"),
        ("NationalWar", "getNationalWarInfo", "description"),
        ("Office", "getCallbackHeroList", "action"),
        ("Office", "getHeroDetail", "hero"),
        ("Office", "getHeroListWithLargessTime", "heroList"),
        ("Buffer", "getHeroBuffer", "bufferList"),
        ("Rank", "getPageRank", "rankList"),
        ("Report", "getReport", "reports"),
        ("School", "getStartTroopInfo", "cityRes"),
        ("Sign", "getSignInfo", "userSignInfo"),
        ("Task", "getTaskDetail", "taskDetail"),
        ("Task", "getTaskInfoList", "taskInfos"),
        ("User", "getNobilityInfo", "nobilityInfo"),
        ("User", "loadHeroDetail", "hero"),
        ("User", "loadHerosDetail", "heroes"),
        ("User", "loadUserGoodsByType", "goodList"),
        ("Hero", "getUserTalentBookList", "userBookList"),
        ("Armor", "getUserArmorList", "armorList"),
        ("Armor", "getUserHorseList", "armorList"),
        ("Ground", "getStartTroopInfoByXY", "gridLevel"),
        ("Ground", "getWoundedSoldier", "soldiers"),
        ("Shop", "getShopInfoByGids", "goodsList"),
        ("Shop", "getMonthCardInfo", "cardInfo"),
        ("Shop", "getShopInfoByType", "vipShopList"),
        ("Trick", "getTrickList", "trickList"),
        ("Union", "getUnionApplyInvite", "applys"),
        ("Union", "getRecommendUnionList", "unionList"),
        ("Union", "getUnionHelpList", "helpList"),
        ("Union", "loadUnionDetail", "union"),
        ("Union", "loadUnionInfo", "union"),
        ("Union", "loadUnionMemberList", "memberList"),
        ("World", "getUnionPowers", "myUnionPowerList"),
        ("World", "getWorldMapOverview", "myCityList"),
    ):
        request = {"type": kind, "action": action}
        if action in {"getCityProduct", "getMilitaryOfCity", "getCitySoldier",
                      "getCitySoldierInfo", "getLevyInfo", "getPacifyPeopleInfo",
                      "getStartTroopInfo"}:
            request["cityId"] = 1001
        response = await dispatch_compat_views(
            request, SESSION
        )
        assert response["ret"] == 0
        assert expected in response


async def test_unknown_compatibility_view_is_not_claimed():
    assert await dispatch_compat_views(
        {"type": "City", "action": "createCity"}, SESSION
    ) is None
