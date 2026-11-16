# Empire War private test server — Phase 1

Python 3.11+ foundations for local security auditing and interoperability research.
This implements the recovered transport, four client cost functions, an atomic
synthetic economy, and minimal synthetic login/resource synchronization. It is
not a complete game server, and actual city-screen compatibility is not yet
established. No official endpoint is contacted by these tools.

The later Phase 1 instructions in the request select **aiosqlite and port 1024**.
PostgreSQL, Redis, the admin API, reward handling, building upgrades, and market
experiments remain subsequent phases. Unknown actions return an explicit local
error instead of reporting fabricated success.

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

`config.yaml` defines explicitly synthetic credentials (`research-local` /
`local-test-only`) and dedicated state/audit paths. The first startup seeds one
research city; later startups never reset balances. Stored passwords use salted
PBKDF2. Changing the credentials in config after initialization intentionally
fails rather than changing the existing account silently. Keep research state
separate from original captures; initialization rejects pre-existing databases
without this emulator's application identifier and schema version.

Use only the synthetic account in a modified client. Login accepts the captured
`User/doLogin` envelope and returns `memCityResource`; `City/synchCityResource`
returns `cityRes`. The account and city IDs are synthetic `1`. The current login
fixture has empty building/hero/config arrays and only troop cost fields in
`cfgSoldier`; it is enough for protocol integration tests, not a verified city
screen. A full client login needs additional decoder/configuration work.

## Existing ewo-proxy integration

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

The proxy compatibility test uses the real existing proxy and verifies the
server's compressed login and uncompressed sync response bytes arrive unchanged.

## Evidence and implementation

| Component | Implemented behavior | Evidence |
| --- | --- | --- |
| `protocol/frame_parser.py` | Five-byte big-endian header; 17-byte repeating XOR; reset offset each payload; no padding; type 8 decompresses before XOR; automatic compression above 4096 UTF-8 bytes | Native `TransportMessage`, `Utils::XOR`; existing proxy codec |
| `economy/time_util.py` | All four requested functions, with original thresholds/evaluation order and float32 intermediates | `gold_logic_focus.c:14,51,93,116`; economy audit |
| `economy/resource_engine.py` | The 32 decoded resource fields plus captured `vacation`; atomic deductions, ownership checks, audit and troop reservations | `MemCityResource::decode`, explicit local transaction policy |
| `economy/troop_config.yaml` | 99 consistent SID base-cost records covering food/wood/iron/rock/gold/people | 28 successful `doLogin.cfgSoldier` captures; row IDs retained |
| `handlers/do_login.py` | Synthetic account and response shape, UUID bound to TCP connection | Captured `User/doLogin` field names; local auth policy |
| `handlers/synch_city_resource.py` | Owned city snapshot under `cityRes` | Captured `City/synchCityResource` responses |

Detailed provenance and limits are in [EVIDENCE.md](EVIDENCE.md). Original
credentials, user identifiers, chat, mail and balances were not copied into
fixtures. The one exception to leaving original tools untouched is the proxy's
opt-out flag. The capture databases and Ghidra exports are read-only inputs.

Rebuild the sanitized fixtures explicitly with:

```powershell
.\.venv\Scripts\python.exe tools/extract_research_fixtures.py new-set-up/game_protocol_v2.db
```

That offline utility keeps only object shapes with empty values and an allowlist
of numeric troop cost fields. Conflicting observed costs fail extraction. It
never imports captured accounts into emulator state.

## Economy guarantees and limits

Each operation gets its own async SQLite connection. `BEGIN IMMEDIATE` serializes
writers; all balances are checked before the first update. The attached `audit.db`
shares the transaction with state and reservation writes. Both files require
DELETE journals and FULL synchronization. SQLite documents cross-file atomicity
for this configuration: [ATTACH](https://www.sqlite.org/lang_attach.html) and
[transaction semantics](https://www.sqlite.org/lang_transaction.html). Database
work uses [aiosqlite](https://aiosqlite.omnilib.dev/en/stable/).

`validate_and_deduct_resources()` is a trusted internal API. No wire handler
accepts client-declared costs. `reserve_troops()` looks up the SID internally and
records a reservation with the same transaction as its deductions. It is not a
completed troop-training handler: draft duration, prerequisites and modifiers
remain unverified. It includes all six captured cost categories, not only three.

The ledger deliberately rounds client float fields to float32. This models the
recovered client type, not proof of the official database's numeric storage.
Non-finite, negative, excessive or unknown costs are rejected. A subtraction too
small to change a float32 balance also fails explicitly. Passive resource growth
and upgrade prices are not inferred. `lastupdate` uses milliseconds, consistent
with captured timestamp scale and the client prediction code.

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

`13003` is observed in draft failures and is used for insufficient resources.
`900101` (local authentication), `900102` (ownership), `900103` (invalid local
cost/input), and `900104` (unimplemented action) are emulator-specific codes,
not claims about official error semantics.

## Isolation and verification scope

The executable entry points install a Python audit hook rejecting non-loopback
connect/bind/DNS/datagram operations. The server has bounded frame expansion,
connection count, session count, per-connection request rate, and read/write
deadlines. Malformed/non-finite/duplicate-key JSON closes only that connection.
The codec intentionally rejects malformed JSON accepted by the old proxy's
relaxed inspection path. Runtime logs contain lifecycle/error classes, not login
packets or passwords. There is no crash/hang simulation in Phase 1.

The audit hook is defense in depth, **not an OS firewall**. Windows firewall rules
were not installed: this machine's venv launches the shared Miniconda process
image, so a rule targeting that image would affect unrelated Python workloads.
OS-level per-emulator egress isolation needs a dedicated executable/runtime or
an isolated VM before the later crash-comparison work. Do not publicly host this
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
