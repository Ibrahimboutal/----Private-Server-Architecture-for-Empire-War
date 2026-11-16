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
