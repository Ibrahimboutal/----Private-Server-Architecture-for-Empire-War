# Client login and startup integration — 16 November 2026

The subsequent actual-client trace passed the version check but received
`doLogin ret=900101`: strict authentication expected the seeded local account.
Added an explicitly configured client adapter that validates then discards the
incoming account strings and selects only the synthetic account. The supplied
`config.yaml` enables it; the library default remains strict. Malformed logins
still revoke the previous session, and city ownership checks remain enforced.
No HTTP account registration or official identity import was added.

Login now includes the eleven reviewed static catalog tables and game version
1719, instead of empty/incomplete catalogs. Added seven authenticated startup
queries with captured wire shapes and empty synthetic mail/events/store data.
No purchase or reward payout is enabled.

Final regression command:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Result: **432 passed in 428.15 seconds**, no skips or failures. Tests cover
strict-mode preservation, the opt-in bridge through the real CLI, no client
credential persistence/echo, session revocation, city ownership, full catalogs
compared with all 28 captured logins, static schema allowlists, startup wire
shapes, real TCP login/startup/sync, and previous economy/market/proxy behavior.
All 54 package/tool/test Python files parse under Python 3.11 grammar; execution
used the existing Python 3.14 virtual environment. `git diff --check` passed.

Deployment and live verification:

- Verified the process owning `127.0.0.1:1024` and restarted only that private
  server. Existing proxy and Android route remained running. Background-process
  metadata and log paths are in `runtime/managed-server.json`.
- `local_smoke_client.py --port 9999 --client-login` passed version handshake,
  dummy-account mapping, full catalogs, all seven startup queries, and sync.
- A dummy-account login sent from inside LDPlayer through owned NAT, ADB reverse
  and the local proxy returned `ret=0`, Research User, synthetic passport, version
  1719, 99 soldier entries and 20 building entries. No official endpoint was used
  and the actual game was not launched or clicked by these probes.
- The ADB diagnostic connection closes after its response, so its proxy
  `transport ended (OSError)` line is not a failed login or process crash.

Read-only crash/main/system log inspection during diagnosis found no matching
Empire War crash marker, and the game process remained alive. The user's trace
therefore establishes a rejected login/disconnection, not its asserted native
crash mechanism. Actual city-screen rendering after the fix remains unverified;
HTTP account/server discovery and registration are still outside this service.

Follow-up native empty-string compatibility correction:

The user's retry still received `Synthetic credentials required` at the
adapter's input guard. Native login encoding permits empty account strings;
those values are irrelevant to the configured synthetic identity. The adapter
now accepts bounded empty strings while strict mode continues rejecting them.
Null/non-string/over-limit fields still fail. The smoke client's `--client-login`
now exercises empty strings rather than nonempty dummy strings.

`pytest tests/test_client_login.py tests/test_cli.py -q`: **22 passed in 110.84
seconds** after this correction. The 432-test full-suite result above preceded
this small correction; these targeted checks exercise both login policies,
actual CLI startup, and economy/market smoke paths. Python 3.11 grammar and diff
checks passed for the correction.

Reloaded only the verified private server, then observed that proxy ports
9999/10001 and the Android reverse mapping had disappeared. Restored the local
proxy after confirming its ports were free, then rebuilt the helper-owned
Android route. The helper stopped only Empire War. Background proxy metadata
is recorded in `runtime/managed-proxy.json`. The direct-server and restored-proxy
empty-string smoke checks both passed all four stages.
The final empty-string login probe from inside LDPlayer also passed over the
restored route, returning Research User, `ret=0`, version 1719 and full catalogs.
Empire War remained stopped after route restoration; city rendering still needs
the user's next game launch. Neither the probe nor route helper launched it.

# Client startup handshake fix — 16 November 2026

The actual LDPlayer client reached the private server but received `900101` for
`User/getServerVersion`, because the router required a session for every action
except login. Read-only capture inspection established that this handshake
precedes login and that all 28 recorded responses agree on version 1719 and the
market URL. The public handler now returns that captured response for the
observed Android/FT/market-0 profile without creating or renewing a session.

Regression command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_startup.py tests/test_server.py tests/test_cli.py -q
```

Result: **47 passed in 183.85 seconds**. Coverage includes pre-login access,
continued gameplay authentication, session preservation, unsupported platforms,
invalid field types, captured startup ordering, and the version→login→sync
sequence through actual mode 4 and mode 8 TCP frames. The real CLI smoke client
now includes the version handshake. Changed Python sources parse under Python
3.11 grammar; `git diff --check` passed.

After tests passed, the exact private-server listener/process was verified and
restarted with the fix, leaving the existing localhost proxy running. The new
server's PID/log metadata is in `runtime/managed-server.json`. Live verification:

- Updated smoke client through `127.0.0.1:9999`: version handshake, synthetic
  login and resource synchronization all passed.
- A version-only synthetic probe inside LDPlayer traversed the installed route
  and received the exact captured `getServerVersion ret=0` response before login.

No official account or endpoint was used by these probes. This resolves the
specific version-handshake rejection in the supplied log; actual stock-client
account login and city-screen loading remain unverified. HTTP startup is still
separate. At that validation stage, local login required the synthetic fixture
credentials; the later optional client adapter is documented above.

# LDPlayer routing validation — 16 November 2026

Diagnosed the running local setup, separately from the synthetic Phase 3 suite:

- ADB device `emulator-5554` had `tcp:1024 -> tcp:9999`, but its NAT OUTPUT chain
  contained no redirect. Windows smoke-client traffic therefore did not establish
  any game-client routing.
- Stopped only `com.feelingtouch.empirewaronline` to end its existing session.
  Confirmed this LDPlayer kernel lacks the iptables `owner` matcher.
- Added `tools/local_android_route.py`, with an owned `EW_LOCAL_TCP` chain,
  per-install comment and ownership record. The existing reverse mapping was
  reused and is not owned by this helper. No broad NAT flush or ADB restart ran.
- Installed the IPv4 TCP destination-port-1024 route to Android loopback and
  verified its packet counter. The rule covers the emulator's traffic on that
  port, rather than a specific app UID.
- A synthetic login sent from Android to `127.0.0.2:1024` traversed DNAT, ADB
  reverse, the localhost proxy and the private server, then decoded successfully.
  The final diagnostic printed `PASS`. No official endpoint was used for this
  probe and no game action was sent.
- Diagnosed ADB shell LF→CRLF expansion on this LDPlayer. A five-byte diagnostic
  isolated it; the probe now protects stdout with base64 while keeping its input
  stream open until a response arrives. Actual game TCP transport is unchanged.

Regression command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_android_route.py -q
```

Result: **20 passed in 1.53 seconds**. Tests mock ADB/iptables and cover route
ownership, pre-existing tunnel preservation, partial-install rollback, conflicts,
idempotent start, device restart recovery, subprocess argument safety, corrupt
ownership records, and binary probe response handling. The previous 307-test
full-suite result below predates this helper; it was not rerun for this isolated
tool/documentation change. The helper/tests also parse with Python 3.11 grammar.

The route is installed and the game was left stopped. This is a verified TCP
bridge, not proof that the stock client loads a city. HTTP startup/server-list
integration and synthetic client login remain unfinished. The port-specific
route is not full emulator network isolation; it does not redirect or block
HTTP/HTTPS, IPv6 or other destination ports.

# Phase 3 validation — 16 November 2026

Final command:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Result: **307 passed in 69.84 seconds**, with no skips or failures. Runtime:
Windows, CPython **3.14.7** in the project `.venv`. All **44** package/tool/test
Python files also parse under Python 3.11 grammar. Python 3.11 runtime execution
remains untested. `pip check`, byte-compilation and `git diff --check` passed.

The new tests cover:

- The supplied defensive pricing model at signed int32 boundaries, fractional
  truncation, multiplication overflow, negative/zero arithmetic, NaN/Infinity,
  huge integers and invalid types. This verifies the local model, not the
  official server's reported crash mechanism.
- The captured one-Food listing, float32-equivalent request prices, owned
  listing and informational response shapes, and building-state market counts.
  Repeated valid requests produce separate paid listings.
- Atomic resource/listing/economy-audit/market-audit commits, concurrent food
  shortfalls, ownership failures, insertion/audit failure rollback, native ID
  bounds and explicit refusal of unsupported expiry behavior.
- Decoded request and frame-metadata recording, recursive credential-field
  redaction, and valid JSON tags for direct non-finite/huge-integer experiments.
- Persistent breaker state across restart, exactly N contained failures under
  concurrent overflows, counter reset before trip, and no rearming after trip.
- Actual mode 4 and mode 8 TCP packets: safe overflow returns `19011` while
  preserving the connection and resource balances; simulated overflow closes
  only its connection after the audit commits. Other sessions remain healthy.
  Database locks are released before the delay, and server shutdown cancels it.
- Gateway rejection of non-finite JSON followed by successful new connections.
  Session and city validation precede arithmetic, market logging and simulation.
- The real proxy preserving market rejection/listing bytes alongside existing
  training, building, login and synchronization exchanges.
- Real CLI execution of the local market client in safe and simulation modes,
  including listing creation/readback and post-boundary server health. The
  gameplay-disabled CLI case still enables market; it is not a both-disabled
  protocol-only test.
- Atomic migration from schema v3 to v4, preservation of existing gameplay and
  audit history, missing/unowned/mismatched database refusal, foreign-key/count
  constraints, and rollback if the audit schema cannot be created.

The focused market/migration run passed **78 tests**, and the wire/proxy/CLI run
passed **24 tests** before the final full suite. Captured market fixture fields,
response shapes and the isolated Food deduction are checked against the original
read-only database. No official endpoint was contacted and no source capture or
Ghidra export was modified.

The simulation waits at most one second asynchronously and closes the triggering
connection. It does not reproduce a real process crash or event-loop hang.
Successful market mutations are restricted to the approved one-Food fixture;
general fees, caravan limits, buying, cancellation, refunds and expiry settlement
remain unsupported. Actual modified-client city-screen compatibility remains
unverified. The existing Python loopback guard is not an OS firewall.

Windows asyncio tests required approved sandbox escalation for loopback socket
access. Mutable data was synthetic and isolated in temporary test databases.
Dependency versions remain aiosqlite 0.22.1, PyYAML 6.0.3, pytest 9.2.0 and
pytest-asyncio 1.5.0.

The sections below record earlier releases. Their then-current limitations are
superseded by the specific Phase 3 results above.

# Phase 2 validation — 16 November 2026

Environment: Windows, CPython **3.14.7**, isolated project `.venv`.
All **35** package, tool and test Python files also parsed successfully using
Python 3.11 grammar. Python 3.11 runtime execution remains untested.

Final command:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Final result: **218 passed in 98.95 seconds**, with no skips or failures.
The earlier focused economy/gameplay/migration run passed 80 tests before the
final boundary corrections; the complete run includes those corrections.

The new coverage verifies:

- Fixed SID 1/2 prices, float32 batch durations, two queue slots, atomic
  deductions, persisted FIFO completion, restart catch-up, and repeated
  completion checks without duplicate troop awards.
- House 1→2 timed/instant upgrades, premium money separate from city gold,
  ignored client prices, captured completion fields, preserved instant-upgrade
  timestamps, and unsupported transitions rejected without a charge.
- The separate Quarry fixture: 822 whole wood units missing, captured price
  of six premium units, partial wood deduction, other full resource deductions,
  and the observed post-completion field changes.
- Concurrent queue/upgrade requests, insufficient funds, ownership, audit/queue
  insertion failures, completion rollback/retry, and exactly-once effects.
  Native bounds are checked before accepting work that would overflow
  completion times, troop totals or resource fields.
- Atomic v1/v2→v3 state/audit migrations, balance/account/history preservation,
  audited millisecond→second conversion, idempotency, audit-failure rollback,
  and refusal of mismatched, missing, unowned or future-version database pairs.
- Binary TCP action handling, worker completion without a client request,
  no request-level deduplication, and Unix-second wire timestamps.
- The actual existing proxy with response rewriting disabled: login, sync,
  draft, House upgrade and queue responses arrive byte-for-byte unchanged.
- Real CLI startup and smoke-client execution for protocol-only, default
  Phase 2 and Quarry configurations. Gameplay variants spend only temporary
  synthetic resources; the Quarry check also verifies the premium debit.

Local evidence tests compare selected House/Quarry quotes, completion deltas,
level 3 previews and troop configuration against the original read-only capture
database. Existing transport tests retain the 100 legacy ciphertext comparisons
and synthetic full-frame vectors described in the historical baseline below.

`pip check` found no broken requirements. Package/tool/test byte-compilation and
`git diff --check` passed. Dependency versions remain aiosqlite 0.22.1,
PyYAML 6.0.3, pytest 9.2.0 and pytest-asyncio 1.5.0.

Windows sandboxing blocks asyncio's loopback socketpair, so network-bearing
tests ran with approved sandbox escalation. Tests used loopback addresses,
synthetic accounts and temporary mutable databases. No official endpoint was
contacted and no original capture database or Ghidra export was modified.

Not established: loading the modified client's city screen; general production,
bonus or shortage formulas beyond the approved fixtures; original full-wire
capture equality; market/crash simulation; reward flows; Redis/PostgreSQL
deployment; or OS-level egress firewall isolation. These are outside this
Phase 2 implementation. The server's Python audit hook is not an OS firewall.

The following section records the earlier Phase 1 baseline. Its limitations
describe that release; the supported Phase 2 flows are validated above.

# Phase 1 validation — 15 November 2026

Environment: Windows, CPython **3.14.7**, isolated project `.venv`.
Source parsing also passed with Python 3.11 grammar for all 19 package/tool
Python files. A Python 3.11 interpreter was not available for runtime testing.

Final command:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Final result: **150 passed in 23.07 seconds**.

The suite includes:

- Synthetic full-frame golden vectors and 100 independently retained legacy
  ciphertext bodies compared with their captured JSON, using read-only access.
- All four TimeUtil boundary sets, float32 behavior and rejected undefined
  conversion domains; decoded resource field names checked against Ghidra.
- 99 troop base-cost entries checked across captured login configurations.
- Concurrent transaction races, all-or-nothing deductions, ownership checks,
  audit failure, reservation failure and pre-commit cancellation rollback.
- Session UUID uniqueness, TTL, connection isolation, authentication, repeated
  requests, fragmented/coalesced frames, limits and malformed input handling.
- Actual loopback TCP login/resource synchronization and the existing proxy
  preserving server response bytes with resource rewriting disabled.
- Real server CLI startup followed by the smoke client, with network audit hooks
  installed in both processes. A separate test verifies external connect is
  rejected by the audit hook before socket network I/O.
- Prompt shutdown of active clients and signed-integer/float conversion
  round-trip limits, added after independent review.

The documented editable installation (`pip install -e ".[test]"`) succeeded.
`pip check` found no broken requirements. Package/tools/tests byte-compilation
passed, and the installed `empire-war-local --help` command succeeded.

Installed principal dependencies: aiosqlite 0.22.1, PyYAML 6.0.3, pytest 9.2.0,
pytest-asyncio 1.5.0. Windows sandboxing blocks asyncio's loopback socketpair;
network-bearing tests ran with approved sandbox escalation. They used only
loopback addresses, synthetic accounts, and isolated temporary mutable databases.

An intermediate run had one ordinary login response exceed a two-second test
deadline. Ordinary response deadlines were adjusted to ten seconds to tolerate
password hashing/disk latency. The separate idle deadline test still uses a
one-second server timeout. The final full run passed without failures.

Not established by these tests: loading the real client's city screen, official
server formulas/validation beyond the supplied observations, original full-wire
capture equality (original headers are unavailable), market/crash simulation,
complete training/building/reward flows, Redis/PostgreSQL deployment, or OS-level
egress firewall isolation. These limits are documented in README.md and EVIDENCE.md.

No original capture database or Ghidra export was modified. The existing proxy
received only the resource-rewrite opt-out flag and its guarded branch. Local
launchers use separate runtime files and never run the official-server launcher.
