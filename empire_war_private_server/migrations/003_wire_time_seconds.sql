-- Correct the Phase 1 millisecond assumption for owned synthetic state only.
-- Captured MemCityResource.lastupdate uses Unix seconds. Retain historical
-- audit entries unchanged, and record each conversion in this same transaction.
-- The bounded range identifies contemporary legacy milliseconds; zero and
-- already-correct seconds are left untouched.
INSERT INTO audit.economy_events
    (created_ms,user_id,city_id,action,before_json,after_json,costs_json)
SELECT CAST(strftime('%s','now') AS INTEGER)*1000,user_id,cid,
       'migrate_lastupdate_seconds',
       '{"lastupdate":' || lastupdate || '}',
       '{"lastupdate":' || CAST(lastupdate/1000 AS INTEGER) || '}',
       '{}'
FROM cities
WHERE lastupdate >= 1000000000000 AND lastupdate < 10000000000000;

UPDATE cities SET lastupdate=CAST(lastupdate/1000 AS INTEGER)
WHERE lastupdate >= 1000000000000 AND lastupdate < 10000000000000;

PRAGMA main.user_version = 3;
PRAGMA audit.user_version = 3;
