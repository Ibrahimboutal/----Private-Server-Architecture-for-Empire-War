"""Build the owned-city fields shared by login and User/getCityInfo."""

from typing import Any

from ..auth.session_manager import Session
from ..economy.gameplay_engine import GameplayEngine
from ..economy.resource_engine import ResourceEngine


async def read_city_state(session: Session, engine: ResourceEngine, gameplay: GameplayEngine | None
                          ) -> tuple[dict[str, Any], int | None]:
    """Rows 23420 and 25106 supply the fields. Values describe the local city."""
    city = {"cid": session.city_id, "uid": session.user_id, "name": "Research City",
            "chiefhid": 0, "attackedEndtime": 0, "province": 0, "state": 0, "type": 0}
    state = {
        "city": dict(city, currentCityCaptiveHeroCount=0, currentCityHeroCount=0),
        "cityList": [dict(city, taketime=0)],
        "attackTroopList": [], "pointHeroIdList": [], "sysCityDefences": [], "sysTechnics": [],
        "sysBuildings": [], "sysCitySoldiers": [], "totalHeroCount": 0, "enemyAlarm": 0,
        "buildingQueueEndtime": 0, "defaultBuildingQueue": 0, "maxBuildingQueue": 0,
    }
    if gameplay is None:
        resource = await engine.get_resources(session.user_id, session.city_id)
        state["memCityResource"] = resource.to_wire()
        return state, None
    snapshot = await gameplay.snapshot(session.user_id, session.city_id)
    state.update(memCityResource=snapshot["resources"], sysBuildings=snapshot["buildings"],
                 sysCitySoldiers=snapshot["troops"], defaultBuildingQueue=1, maxBuildingQueue=1)
    return state, snapshot["money"]
