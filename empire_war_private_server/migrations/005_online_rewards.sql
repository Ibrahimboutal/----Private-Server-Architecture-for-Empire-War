-- Synthetic inventory and a single persistent captured reward progression.
-- No daily reset is inferred. State, items, resources and audit commit together.
CREATE TABLE city_goods (
    city_id INTEGER NOT NULL REFERENCES cities(cid),
    gid INTEGER NOT NULL CHECK(gid > 0 AND gid <= 2147483647),
    count INTEGER NOT NULL CHECK(count >= 0 AND count <= 2147483647),
    PRIMARY KEY(city_id, gid)
);
CREATE TABLE online_reward_state (
    city_id INTEGER PRIMARY KEY REFERENCES cities(cid),
    step INTEGER NOT NULL CHECK(step BETWEEN 0 AND 6),
    ready_at_s INTEGER NOT NULL CHECK(ready_at_s >= 0 AND ready_at_s <= 2147483647)
);
CREATE TABLE online_reward_claims (
    city_id INTEGER NOT NULL REFERENCES cities(cid),
    step INTEGER NOT NULL CHECK(step BETWEEN 0 AND 5),
    claimed_at_s INTEGER NOT NULL CHECK(claimed_at_s >= 0),
    PRIMARY KEY(city_id, step)
);
PRAGMA main.user_version = 5;
PRAGMA audit.user_version = 5;
