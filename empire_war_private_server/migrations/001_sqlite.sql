-- Local research schema, v1. Execute with the separate audit database attached.
PRAGMA user_version = 1;
PRAGMA application_id = 1163350098;
PRAGMA audit.user_version = 1;
PRAGMA audit.application_id = 1163350098;
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    passport TEXT NOT NULL UNIQUE,
    password_salt TEXT NOT NULL,
    password_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cities (
    cid INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    changing INTEGER NOT NULL, chiefLoyalty INTEGER NOT NULL, complaint INTEGER NOT NULL,
    food REAL NOT NULL CHECK(food>=0), foodAdd REAL NOT NULL, foodArmyUse REAL NOT NULL,
    foodArmyUseRate INTEGER NOT NULL, foodMax REAL NOT NULL,
    gold REAL NOT NULL CHECK(gold>=0), goldMax REAL NOT NULL, goldRate INTEGER NOT NULL,
    heroFee REAL NOT NULL,
    iron REAL NOT NULL CHECK(iron>=0), ironAdd REAL NOT NULL, ironMax REAL NOT NULL,
    lastupdate INTEGER NOT NULL, morale INTEGER NOT NULL, moraleStable INTEGER NOT NULL,
    people INTEGER NOT NULL CHECK(people>=0), peopleBuilding INTEGER NOT NULL,
    peopleStable INTEGER NOT NULL, peopleMax INTEGER NOT NULL, peopleWorking INTEGER NOT NULL,
    rock REAL NOT NULL CHECK(rock>=0), rockAdd REAL NOT NULL, rockMax REAL NOT NULL,
    tax INTEGER NOT NULL, wood REAL NOT NULL CHECK(wood>=0), woodAdd REAL NOT NULL,
    woodMax REAL NOT NULL, vacation INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS training_reservations (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
    city_id INTEGER NOT NULL REFERENCES cities(cid), sid INTEGER NOT NULL,
    soldier_count INTEGER NOT NULL CHECK(soldier_count>0), audit_id INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS audit.economy_events (
    id INTEGER PRIMARY KEY, created_ms INTEGER NOT NULL,
    user_id INTEGER NOT NULL, city_id INTEGER NOT NULL, action TEXT NOT NULL,
    before_json TEXT NOT NULL, after_json TEXT NOT NULL, costs_json TEXT NOT NULL
);
