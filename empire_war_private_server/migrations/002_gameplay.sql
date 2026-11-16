-- Phase 2 capture-derived gameplay state. Run inside the shared state/audit
-- transaction; do not use executescript, which commits an existing transaction.
ALTER TABLE users ADD COLUMN money INTEGER NOT NULL DEFAULT 0 CHECK(money >= 0);

CREATE TABLE buildings (
    id INTEGER PRIMARY KEY,
    cid INTEGER NOT NULL REFERENCES cities(cid),
    bid INTEGER NOT NULL CHECK(bid > 0),
    level INTEGER NOT NULL CHECK(level >= 0),
    xy INTEGER NOT NULL,
    inner INTEGER NOT NULL CHECK(inner IN (0, 1)),
    state INTEGER NOT NULL DEFAULT 0,
    state_start_s INTEGER NOT NULL DEFAULT 0 CHECK(state_start_s >= 0),
    state_end_s INTEGER NOT NULL DEFAULT 0 CHECK(state_end_s >= 0),
    original_end_s INTEGER NOT NULL DEFAULT 0 CHECK(original_end_s >= 0),
    target_level INTEGER CHECK(target_level >= 0),
    UNIQUE(cid, xy, inner)
);

CREATE TABLE troops (
    id INTEGER PRIMARY KEY,
    cid INTEGER NOT NULL REFERENCES cities(cid),
    sid INTEGER NOT NULL CHECK(sid > 0),
    count INTEGER NOT NULL CHECK(count >= 0),
    UNIQUE(cid, sid)
);

CREATE TABLE draft_queues (
    qid INTEGER PRIMARY KEY AUTOINCREMENT,
    cid INTEGER NOT NULL REFERENCES cities(cid),
    building_id INTEGER NOT NULL REFERENCES buildings(id),
    sid INTEGER NOT NULL CHECK(sid > 0),
    soldier_count INTEGER NOT NULL CHECK(soldier_count > 0),
    duration_s INTEGER NOT NULL CHECK(duration_s >= 0),
    state INTEGER NOT NULL CHECK(state IN (0, 1, 2)),
    start_s INTEGER NOT NULL DEFAULT 0 CHECK(start_s >= 0),
    end_s INTEGER NOT NULL DEFAULT 0 CHECK(end_s >= 0),
    audit_id INTEGER NOT NULL
);
CREATE INDEX draft_queues_city_state ON draft_queues(cid, state, qid);
CREATE INDEX draft_queues_building_state ON draft_queues(building_id, state, qid);
CREATE UNIQUE INDEX draft_one_active_per_building ON draft_queues(building_id) WHERE state=1;

CREATE TABLE scenario_cities (
    cid INTEGER PRIMARY KEY REFERENCES cities(cid),
    scenario_id TEXT NOT NULL
);

-- These values become visible together only when the enclosing transaction
-- commits successfully. Failure rolls back DDL, data changes, and both versions.
PRAGMA main.user_version = 2;
PRAGMA audit.user_version = 2;
