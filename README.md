# Empire War private test server

The Python 3.11+ server runs on **127.0.0.1:1024** for local interoperability
and security research. It supports captured training/building scenarios and a
Phase 3 market fixture with overflow protection, audited experiments and bounded
connection-failure simulation. It uses the recovered binary protocol, four
client cost functions, and SQLite transactions across state and audit files.

The default `gameplay_scenario: captured-phase2-v1` selects fixed quotes from
the local captures. `config.shortage.yaml` selects a separate Quarry experiment
with one observed wood shortfall. Set both `gameplay_scenario: disabled` and
`market_scenario: disabled` for the original login and resource-sync behavior.
Additional troops, levels and bonus formulas
require more evidence. The current release covers the loopback TCP service.
Compatibility with a modified client's city screen still needs a client test.

## Run

From this repository in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m empire_war_private_server.main --config config.yaml
```

The server binds only to `127.0.0.1:1024`. Another terminal can run:

```powershell
.\.venv\Scripts\python.exe tools/local_smoke_client.py
.\.venv\Scripts\python.exe -m pytest -q
```

On a fresh Phase 2 city, add `--exercise-gameplay` to the smoke client to
spend synthetic resources on training and a timed House upgrade. With
`--config config.shortage.yaml --exercise-gameplay`, it exercises the captured
Quarry instant upgrade. Add `--port 9999` to run either check through the local
proxy. These optional checks mutate the selected test state and need a fresh
level 1 building; point both database paths at a new pair for another run.

`config.yaml` defines explicitly synthetic credentials (`research-local` /
`local-test-only`) and dedicated state/audit paths. The first startup seeds one
research city. Later startups reuse its balances and progress. Stored passwords use salted
PBKDF2. Changing the credentials in config after initialization intentionally
fails rather than changing the existing account silently. Keep research state
separate from original captures; initialization rejects pre-existing databases
without this emulator's application identifier and schema version.

The supplied `config.yaml` now enables `synthetic_client_login: true` for
LDPlayer testing. Any well-formed `User/doLogin` packet selects the same seeded
Research User and Research City, after discarding the incoming account strings.
This is an explicit local adapter, not official authentication or registration.
No official identity or password is copied into the emulator's database. The
library default and other scenario configs keep this adapter disabled; set the
flag to `false` to require the exact synthetic credentials. Restart the server
after changing this flag. Creating an account through the game's HTTP service
is still outside this TCP implementation.

The native client can send empty account strings. The adapter accepts those
strings too; strict mode still requires nonempty matching credentials. Verify
the adapter through the proxy with empty native-style account strings:

```powershell
.\.venv\Scripts\python.exe tools/local_smoke_client.py --port 9999 --client-login
```

Use the synthetic account for these examples. `User/doLogin` returns
`memCityResource`, while `City/synchCityResource` returns `cityRes`. The
seeded user and its database city key are both `1`. Android login advertises
city ID `1001`, the synthetic map square `(1, 1)`. Read `city.cid` from
login for subsequent requests. The older research examples below start with
city ID `1`. A client that requests `City/dummyChangePosition` uses the
returned `targetCid` for `User/getCityInfo` and later requests on that connection.
With gameplay enabled, login also returns
`sysBuildings`, `sysCitySoldiers` and `user.money`. Login also returns the full
captured static display catalogs and `gameVersion: 1719`, including 99 soldier
types and 20 building types. Those display entries do not enable additional
mutations: training/upgrades remain limited to the selected scenario. Read building instance IDs from
`sysBuildings[].id` on login.

The Android client first sends this public TCP handshake, before `doLogin`:

```json
{"type":"User","action":"getServerVersion","marketId":0,"packageChannel":"FT","phoneOs":"android"}
```

The server returns the captured `ret: 0`, `version: 1719` and `marketApkUrl`.
This check does not create or renew a session; gameplay still requires login.
Only the observed Android/FT/market-0 profile is enabled. The smoke client now
checks this handshake before its synthetic login. If the proxy shows
`getServerVersion ret=900101`, the server process is running older code and
must be restarted. Restarting just the proxy does not reload server handlers.

After login, the server acknowledges device metadata without retaining it and
serves synthetic empty mail, event and store status for the captured startup
queries. APK-upgrade acknowledgement supports the captured Android 11.520
profile without issuing rewards. Payment offers, purchases, account creation
and reward claims are not enabled by these responses.

## Phase 2 scenario

The fresh city has a level 1 House at `(x=0, y=2, inner=1)`, a level 1 Barracks
at `(x=4, y=4, inner=1)`, and 100 synthetic premium money. City gold is a
separate resource. The test scenario combines the barracks quote from capture
row 14481 with the House quote from row 24882. [Capture provenance](EVIDENCE.md)

| Action | Food | Wood | Iron | Rock | People | Time |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Train one Peasant (`sid=1`) | 50 | 150 | 10 | 0 | 1 | 43 seconds |
| Train one Auxiliary (`sid=2`) | 80 | 100 | 50 | 0 | 1 | 21 seconds |
| Upgrade House from level 1 to 2 | 200 | 1,000 | 100 | 200 | 0 | 60 seconds |

The server computes a batch duration by multiplying the captured `draftTime`
by `soldierCount` in float32, then truncating to whole seconds. For example, 50
Auxiliaries train in 1,086 seconds. The Barracks accepts 1–50 soldiers per batch
and holds one active batch plus one pending batch. Resources are charged when
the server accepts the request. The city gains troops when the batch finishes.

### Example requests

Each line below is one JSON object to encode with `FrameCodec.encode_payload()`.
Send one frame at a time on the same authenticated TCP connection and decode
each reply with `FrameCodec.read_frame()`. The `qid` and building instance IDs
in this example assume a fresh database. Use the IDs returned by your server.

```jsonl
{"type":"User","action":"doLogin","passport":"research-local","password":"local-test-only"}
{"type":"Army","action":"startDraftQueue","cityId":1,"sid":2,"soldierCount":1,"x":4,"y":4}
{"type":"User","action":"getAllQueue","cityId":1}
{"type":"User","action":"checkAQueue","cityId":1,"queueId":1,"queueType":1}
{"type":"Building","action":"getBuildingState","cityId":1}
{"type":"Building","action":"getBuildingInfo","cityId":1,"uniqueId":2}
```

`startDraftQueue.queues[].qid` supplies the ID for a draft completion check.
`needTry` is true while the batch is pending or active. On completion, observe
the new count in the barracks' `armys` list or on a subsequent login. Repeating
the check is safe. Repeating `startDraftQueue` requests a second paid batch.

For the timed House upgrade, send the first request below, then check using the
`building.id` in its reply. `buildingId=5` in the upgrade request is the House
**type**. `queueId` for `queueType=3` is the building **instance** ID.

```jsonl
{"type":"Building","action":"upgrade","cityId":1,"buildingId":5,"inner":1,"x":0,"y":2}
{"type":"User","action":"checkAQueue","cityId":1,"queueId":1,"queueType":3}
```

The timed reply includes `building` at level 1 in state 1 and the post-charge
`cityResource`. After 60 seconds the building is level 2 in state 0. The city's
`peopleMax` increases by 200 and `peopleStable` by 180. To exercise
the funded instant variant, use a fresh level 1 House in a separate test state
and audit database pair. Send this request in place of the timed upgrade.

```json
{"type":"Building","action":"upgradeImmediatelyByGoldWhenLackRes","cityId":1,"buildingId":5,"inner":1,"x":0,"y":2,"clientMoneyCost":0}
```

For the fully funded House quote, this charges the same resource costs plus 1
premium money. The server calculates that fee, returns `goldCost: 1`, and
updates `user.money` from 100 to 99. The supplied `clientMoneyCost` has no effect.
The immediate response reports the prior population fields and the next sync
shows both increases, matching rows 26551 and 26554. The default House profile
rejects an instant-upgrade resource shortfall with `ret: 900105`; a normal
upgrade with insufficient resources returns `10004`.

### Quarry wood-shortage experiment

Stop the House server before starting the supplied Quarry configuration on the
same loopback port. This profile stores synthetic state in `runtime/quarry/state.db`
and `runtime/quarry/audit.db`.

```powershell
.\.venv\Scripts\python.exe -m empire_war_private_server.main --config config.shortage.yaml
```

Log in on the new connection and submit these frames. The Quarry at `(5, 1)`
is outside the city (`inner=0`). Its level 2 quote needs 1,000 wood. The fresh
scenario provides 178 wood and sufficient food, rock and iron.

```jsonl
{"type":"User","action":"doLogin","passport":"research-local","password":"local-test-only"}
{"type":"Building","action":"upgradeImmediatelyByGoldWhenLackRes","cityId":1,"buildingId":3,"inner":0,"x":5,"y":1,"clientMoneyCost":0}
{"type":"City","action":"synchCityResource","cityId":1}
```

For the captured shortfall of 822 whole wood units the server debits 178 wood,
360 food, 300 rock and 800 iron, upgrades the Quarry from level 1 to level 2,
and returns `goldCost: 6`. Premium money decreases from 100 to 94. The fixed
quote applies to that shortfall and requires all other costs to be covered.
The next resource sync shows the observed changes of +40 `peopleWorking`, +266
`rockAdd` and +20,000 `rockMax`. Building info can preview the captured House
and Quarry level 3 quotes. Upgrade mutations are supported through level 2.
Other shortages return `900105`. [Quarry capture evidence](EVIDENCE.md)

## Phase 3 market research

The default `market_scenario: captured-market-food-v1` enables one captured
listing: one Food, `resType=0`, `resCount=1`, `hour=12`, `unionSel=0`, and total
asking `gold` equivalent to float32 `0.12`. The captured wire value is
`0.119999997317791`. The listing debits one Food; it does not debit city gold,
other resources or premium money. Each accepted repeat creates another paid
listing; request IDs do not deduplicate it.

```jsonl
{"type":"Market","action":"getUserSellData","cityId":1}
{"type":"Market","action":"sellToUser","cityId":1,"resType":0,"resCount":1,"gold":0.12,"hour":12,"unionSel":0}
{"type":"Market","action":"doGetCityTrade","cityId":1}
{"type":"Building","action":"getBuildingState","cityId":1}
```

Send these after synthetic login on the same connection. `getUserSellData`
returns the captured informational ranges inside `citySellData`; those ranges
do not enable other listing combinations. `sellToUser` returns `trade`.
`doGetCityTrade` returns `tradingList`, whose entries omit `tooLong` and add
`originalEndtime`/`starttime`. Building state reports retained listings in
`market.myCount`. IDs, absolute dates, union IDs and distance are synthetic.

The local limit is 128 retained listings per city, returning `900106` when
reached. This is a storage/response bound, not a recovered caravan limit.
Buying, cancellation, refunds and expiration settlement remain unsupported.
Once any retained listing reaches its captured 43,200-second lifetime, the
own-list query returns `900105` without changing resources or inventing an
expiry transition. Other valid but uncaptured listing requests return `900105`.

The defensive helper `calculate_market_price_safe()` evaluates the supplied
research model `(gold * hour) * res_count` in binary64, checks finite values and
signed int32 bounds before truncating, and returns `19011` on overflow. This
product is separate from official market pricing: the client export and
captures establish that request `gold` is already the **total asking price**.
They do not establish the official server's overflow mechanism. No independent
`gold` crash threshold is assumed. The authenticated wire handler also rejects
invalid domains, including negative prices, before listing anything. Malformed
or non-finite wire JSON is rejected by the protocol layer before market audit.

With the default `simulate_int32_crash: false`, overflowing arithmetic returns
`ret: 19011` and the connection remains available. To compare the bounded
failure behavior, stop the default server and use the separate research pair:

```powershell
.\.venv\Scripts\python.exe -m empire_war_private_server.main --config config.market-research.yaml
# Another terminal:
.\.venv\Scripts\python.exe tools/local_market_client.py --config config.market-research.yaml
```

This configuration uses `runtime/market-research/state.db` and its paired
`audit.db`, sets simulation true, uses threshold 3, and delays each simulated
failure by 100 ms. Despite the compatibility flag's name, simulation means an
async delay followed by closing **only that connection**, never a process crash
or event-loop hang. The delay is configurable from 0 to 1,000 ms.

The global breaker and consecutive-overflow counter persist in the state DB.
The Nth consecutive overflow commits its audit, trips the breaker, and causes
one final simulated disconnect. Subsequent overflows return `19011`, including
after restart. A valid non-overflow request resets the counter before a trip;
it never rearms a tripped breaker. Use a new state/audit pair for a new armed
experiment. Changing the flag or restarting does not clear the latch.
Authentication and city ownership are checked before arithmetic, logging or
simulation; the emulator does not reproduce unauthenticated processing.

`audit.market_events` records decoded requests with recognized credential fields
redacted recursively, frame metadata, outcomes, safe totals, simulation decisions
and breaker state. It retains research fields and nested experiment details,
not raw ciphertext that could retain redacted credentials. Listing deductions,
listing creation, economy audit and market audit share a transaction. Overflow
events and breaker changes commit before the gateway delays or disconnects.

Against the default safe server, run `tools/local_market_client.py` without a
config override. The client sends three synthetic boundary cases and accepts a
bounded disconnect only when its configuration enables simulation. Add
`--exercise-listing` to spend one synthetic Food and check listing/readback;
add `--port 9999` to exercise the unchanged local proxy wrapper. Every client
connection is explicitly to `127.0.0.1`.

## Existing ewo-proxy integration

### LDPlayer routing

The Android Wi-Fi **Manual proxy** setting is for HTTP. It does not route this
game's binary TCP socket. Use **Proxy: None** for this local setup. The Windows
server and proxy remain bound to `127.0.0.1`; entering a Windows LAN address in
Android does not make them reachable on that address.

`adb reverse tcp:1024 tcp:9999` provides a tunnel for Android connections to
its local port 1024. It does not change the game's selected destination.
The separate route helper adds an owned DNAT rule for that missing step:

```powershell
# Keep the local server and tools/local_proxy.py running in their terminals.
.\.venv\Scripts\python.exe tools/local_android_route.py start --serial emulator-5554
.\.venv\Scripts\python.exe tools/local_android_route.py status --serial emulator-5554
.\.venv\Scripts\python.exe tools/local_android_route.py probe --serial emulator-5554
```

`start` verifies the host proxy's synthetic login, stops only Empire War to end
any existing connection, then creates the `EW_LOCAL_TCP` NAT chain. This LDPlayer
has no `iptables owner` matcher, so the rule covers **all emulator IPv4 TCP
connections to destination port 1024**. It rewrites them to Android loopback,
then the ADB tunnel carries them to Windows loopback port 9999. Other ports are
unchanged. The helper never launches the game or changes its HTTP startup data.

`probe` sends a synthetic login from Android `127.0.0.2:1024`; this exercises both
the DNAT rule and the tunnel without an external destination. It prints `PASS`
only after decoding the private server's synthetic login response. This device's
ADB shell rewrites newlines, so the diagnostic base64-encodes its output; actual
game TCP frames are not changed. A passing Windows smoke test alone proves only
the Windows proxy/server path, not emulator routing or actual game readiness.

Ownership is recorded beside the configured state DB. Cleanup removes only this
helper's rules and any reverse mapping it created. A matching pre-existing
reverse mapping is preserved:

```powershell
.\.venv\Scripts\python.exe tools/local_android_route.py stop --serial emulator-5554
```

Cleanup also stops Empire War rather than letting an existing session reconnect
to its normal endpoint. After LDPlayer restarts, inspect `status`; if the owned
record remains but device rules disappeared, run `stop`, then `start` to rebuild.
Do not run the old capture launcher for this setup: it defaults to an external
upstream and writes the original capture database.

**Client readiness:** the native game also uses HTTP account/server discovery
before its TCP login. That HTTP service is not implemented here. The default
config's local login adapter handles clients that already reach `User/doLogin`,
but does not redirect account creation or server discovery. A successful
version/login/sync probe is not proof that the stock client loads its city.
This route also does not block HTTP, HTTPS, IPv6 or other ports; it is not
complete emulator egress isolation. Avoid the game's official account creation
flow for these local tests; full offline startup still needs local integration.

An existing client may already have selected/cached a server and reach the
private TCP listener. Seeing its `getServerVersion` request proves this path;
it does not establish that HTTP startup or subsequent account login is local.

### Windows proxy

The existing proxy has an unconditional resource response rewrite in its normal
mode. A small opt-out (`--disable-resource-rewrite`) was added without changing
that default. Use the dedicated local launcher for emulator tests:

```powershell
# First terminal: leave the emulator running on 1024.
# Second terminal:
.\.venv\Scripts\python.exe tools/local_proxy.py
# Third terminal:
.\.venv\Scripts\python.exe tools/local_smoke_client.py --port 9999
```

This wrapper explicitly routes `127.0.0.1:9999` to `127.0.0.1:1024`, disables
resource rewriting, leaves injection/isolation-test modes off, uses control
port `10001`, and saves redacted captures under `runtime/proxy`. It installs the
same loopback guard as the server. It does not run the normal launcher or change
ADB/NAT rules. If a port is occupied, it fails; it does not terminate other
processes. Stop each foreground process with Ctrl+C.

The proxy compatibility tests use the real existing proxy and verify that
login, sync, draft, upgrade, queue, market rejection and listing response bytes
arrive unchanged.

## Evidence and implementation

| Component | Implemented behavior | Evidence |
| --- | --- | --- |
| `protocol/frame_parser.py` | Five-byte big-endian header; 17-byte repeating XOR; reset offset each payload; no padding; type 8 decompresses before XOR; automatic compression above 4096 UTF-8 bytes | Native `TransportMessage`, `Utils::XOR`; existing proxy codec |
| `economy/time_util.py` | All four requested functions, with original thresholds/evaluation order and float32 intermediates | `gold_logic_focus.c:14,51,93,116`; economy audit |
| `economy/resource_engine.py` | The 32 decoded resource fields plus captured `vacation`; atomic deductions, ownership checks, audit and troop reservations | `MemCityResource::decode`, explicit local transaction policy |
| `economy/troop_config.yaml` | 99 consistent SID base-cost records covering food/wood/iron/rock/gold/people | 28 successful `doLogin.cfgSoldier` captures; row IDs retained |
| `handlers/do_login.py` | Synthetic account and response shape, UUID bound to TCP connection | Captured `User/doLogin` field names; local auth policy |
| `economy/client_catalog.py` | Full static display tables and version 1719; preserves catalogs when a gameplay scenario selects a subset | All 28 successful login captures; fixed row 4 monster variant |
| `handlers/client_startup.py` | Device acknowledgement and empty synthetic mail/events/store status | Captured response shapes, with local inactive fixture values |
| `handlers/synch_city_resource.py` | Owned city snapshot under `cityRes` | Captured `City/synchCityResource` responses |
| `economy/gameplay_engine.py` and `handlers/gameplay.py` | Draft charges, queue completion, building upgrades and queue queries for the selected scenario | Draft rows 14481/14491, House rows 24882/24885/24904, client decoders |
| `data/phase2_scenario.json` | Fixed barracks, House and Quarry quotes/effects with source row IDs | Generated offline by `tools/extract_phase2_profile.py` |
| `economy/market_engine.py` and `data/market_scenario.json` | Fixed one-Food listing, owned queries, atomic audit and persistent breaker | Rows 19112/19126/19127/19135; resource comparison 19104/19119/19132 |
| `vuln_research.py` | Checked binary64 research product with signed int32 output bounds | Defensive model supplied in the research request; not an official formula claim |

Detailed provenance and limits are in [EVIDENCE.md](EVIDENCE.md). Original
credentials, user identifiers, chat, mail and balances were not copied into
fixtures. The one exception to leaving original tools untouched is the proxy's
opt-out flag. The capture databases and Ghidra exports are read-only inputs.

Rebuild the sanitized fixtures explicitly with:

```powershell
.\.venv\Scripts\python.exe tools/extract_research_fixtures.py new-set-up/game_protocol_v2.db
.\.venv\Scripts\python.exe tools/extract_phase2_profile.py new-set-up/game_protocol_v2.db
.\.venv\Scripts\python.exe tools/extract_market_profile.py new-set-up/game_protocol_v2.db
.\.venv\Scripts\python.exe tools/extract_client_catalog.py new-set-up/game_protocol_v2.db
```

The first utility keeps empty object shapes and numeric base cost fields. The
second exports selected static gameplay configuration and the fixed quotes.
The third validates and exports the one-Food market fixture. The fourth retains
only reviewed static catalog fields, excluding account data and endpoints. All read the
capture database in read-only mode. The runtime uses the bundled
fixtures and persists synthetic accounts in the configured state database.

## Economy guarantees and limits

Each operation gets its own async SQLite connection. `BEGIN IMMEDIATE` serializes
writers; all balances are checked before the first update. The attached `audit.db`
shares the transaction with state and reservation writes. Both files require
DELETE journals and FULL synchronization. SQLite documents cross-file atomicity
for this configuration: [ATTACH](https://www.sqlite.org/lang_attach.html) and
[transaction semantics](https://www.sqlite.org/lang_transaction.html). Database
work uses [aiosqlite](https://aiosqlite.omnilib.dev/en/stable/).

On startup versioned migrations upgrade a matching version 1, 2 or 3 state/audit
pair to version 4. Existing balances and audit events survive migration.
`002_gameplay.sql` adds buildings, troop counts, draft queues, scenario membership
and premium money. `003_wire_time_seconds.sql` converts historical Phase 1
millisecond `cities.lastupdate` values to Unix seconds and appends a conversion
audit entry. `004_market.sql` adds listings, persistent research state and the
separate market-event audit table. Historical audit entries retain their values.
A failed migration rolls back the whole pair.
The server rejects a missing counterpart, mismatched versions and unowned files.
To start a separate experiment, point **both** config paths to fresh files.

`GameplayEngine.start_draft()` validates all six resource categories and records
the deduction, queue and audit entries in one transaction. Completion updates
troop counts, the queue state and audit records together. The worker checks for
due work each second, and gameplay reads also settle due work. After a restart,
the next batch uses its predecessor's scheduled finish time. Checking an
already completed queue returns its completion state without issuing more troops.
The same transaction boundary applies to the House upgrade, including premium
money for an immediate upgrade, and the Quarry shortage fixture. These
operations resolve costs internally. Before accepting paid work, the engine
checks that completion times, population effects and troop totals (including
pending batches) will fit the recovered native types.

The Phase 1 `reserve_troops()` method remains an internal API for recording base
cost reservations. Use `Army/startDraftQueue` to exercise a timed training queue.

The ledger deliberately rounds client float fields to float32. This models the
recovered client type, not proof of the official database's numeric storage.
Non-finite, negative, excessive or unknown costs are rejected. A subtraction too
small to change a float32 balance also fails explicitly. Passive resource growth
and general upgrade pricing remain outside the fixed scenario. Login and sync
return `gameTime`, `serverTime`, and resource `lastupdate` in Unix seconds.
The audit table uses `created_ms` for local event timestamps in milliseconds.
Building-info status flags, prerequisites and level 3 previews are retained
from the selected captures. They are informational fixed-scenario data; the
server rejects upgrade transitions beyond level 2. The Barracks view retains
its captured satisfied Muster Field prerequisite without simulating that
building's wider mechanics. Updated production fields are displayed but do not
generate passive resources in this release.

The TimeUtil ports preserve negative-input branches for analysis, including
the finish-now price drop from 75 at 959 seconds to 70 at 960 seconds. Signed
overflow, out-of-range narrowing and undefined conversions raise `CostDomainError`.
They do not invent wrapping prices for undefined domains. These are client
helpers; their existence does not prove the server uses the same formulas.

Sessions last one hour, remain bound to their connection and are revoked on
disconnect/re-login. No request-level deduplication is added. Repeating a valid
internal reservation deducts twice. If a connection fails after commit, the
caller must inspect persisted state before retrying; receipt is not guaranteed.
Cancellation before commit rolls back. Cancellation while `aiosqlite` has already
queued COMMIT can still leave that commit successful; state and audit remain
atomic, but the caller's outcome is unknown and must be inspected before retry.

| `ret` | Meaning in the emulator |
| ---: | --- |
| 10004 | Insufficient city resources for a normal building upgrade |
| 13000 | No Barracks at the requested coordinates (observed draft failure) |
| 13003 | Insufficient resources for training (observed draft failure) |
| 19004 | Insufficient Food for the captured market listing |
| 19011 | Market research arithmetic exceeds its safe domain |
| 900001 | Insufficient premium money for an immediate upgrade |
| 900101 | Local session or authentication failure |
| 900102 | Session does not own the requested city or object |
| 900103 | Invalid input, count, cost or building location |
| 900104 | Action unavailable in this server or configuration |
| 900105 | No approved quote for the requested scenario |
| 900106 | Queue is full, building is busy, or local listing limit is reached |

Codes 900101–900106 describe emulator policies. An unsupported quote or failed
transaction leaves the requested mutation uncommitted.

## Isolation and verification scope

The executable entry points install a Python audit hook rejecting non-loopback
connect/bind/DNS/datagram operations. The server has bounded frame expansion,
connection count, session count, per-connection request rate, and read/write
deadlines. Malformed/non-finite/duplicate-key JSON closes only that connection.
The codec intentionally rejects malformed JSON accepted by the old proxy's
relaxed inspection path. Runtime logs contain lifecycle/error classes, not login
packets or passwords. The market's separate audit retains redacted experiment
details; its optional failure simulation remains bounded to one connection.

The audit hook is defense in depth, **not an OS firewall**. Windows firewall rules
were not installed: this machine's venv launches the shared Miniconda process
image, so a rule targeting that image would affect unrelated Python workloads.
OS-level per-emulator egress isolation needs a dedicated executable/runtime or
an isolated VM. Do not publicly host this
research service. No conclusion about legal exemptions is implied by the code.

Tests cover fixed synthetic vectors, legacy capture bodies, capture schema/cost
consistency, cancellation, audit-failure rollback, concurrent insufficient-funds
races, authentication, TTL, malformed-frame recovery, local TCP integration and
the actual proxy. The retained legacy capture bodies do not include original
headers; tests reconstruct those headers. `game_protocol_v2.db` omits raw bytes,
so it supports structural/semantic comparisons, not original-frame equality.
Neither official-server behavior nor a modified game client's city screen was
tested. Test results and interpreter details are recorded in
[VALIDATION.md](VALIDATION.md).
