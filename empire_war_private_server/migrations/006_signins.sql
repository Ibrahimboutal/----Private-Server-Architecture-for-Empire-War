-- Durable local daily sign-in state.
CREATE TABLE daily_signins (
    cid INTEGER PRIMARY KEY REFERENCES cities(cid),
    sign_count INTEGER NOT NULL DEFAULT 0 CHECK(sign_count >= 0),
    last_day INTEGER
);

PRAGMA main.user_version = 6;
PRAGMA audit.user_version = 6;
