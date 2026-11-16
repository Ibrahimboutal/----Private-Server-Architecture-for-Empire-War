-- Local Phase 3 listings and research audit. Execute under the shared
-- state/audit transaction so all schema and version changes commit together.
CREATE TABLE market_listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    cid INTEGER NOT NULL REFERENCES cities(cid),
    res_type INTEGER NOT NULL,
    res_count INTEGER NOT NULL CHECK(res_count > 0),
    gold REAL NOT NULL CHECK(gold >= 0),
    hour REAL NOT NULL CHECK(hour > 0),
    unit_price REAL NOT NULL CHECK(unit_price >= 0),
    generate_time_s INTEGER NOT NULL CHECK(generate_time_s >= 0),
    limit_time_s INTEGER NOT NULL CHECK(limit_time_s >= 0),
    state INTEGER NOT NULL DEFAULT 0,
    trade_json TEXT NOT NULL
);
CREATE INDEX market_listings_city ON market_listings(cid, id);

CREATE TABLE market_research_state (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    consecutive_overflows INTEGER NOT NULL DEFAULT 0 CHECK(consecutive_overflows >= 0),
    breaker_tripped INTEGER NOT NULL DEFAULT 0 CHECK(breaker_tripped IN (0, 1))
);
INSERT INTO market_research_state(singleton) VALUES (1);

CREATE TABLE audit.market_events (
    id INTEGER PRIMARY KEY,
    created_ms INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    city_id INTEGER NOT NULL,
    packet_json TEXT NOT NULL,
    frame_json TEXT NOT NULL,
    outcome TEXT NOT NULL,
    ret INTEGER NOT NULL,
    total_value INTEGER,
    simulated INTEGER NOT NULL CHECK(simulated IN (0, 1)),
    breaker_tripped INTEGER NOT NULL CHECK(breaker_tripped IN (0, 1)),
    consecutive_overflows INTEGER NOT NULL CHECK(consecutive_overflows >= 0)
);
CREATE INDEX audit.market_events_created ON market_events(created_ms, id);

PRAGMA main.user_version = 4;
PRAGMA audit.user_version = 4;
