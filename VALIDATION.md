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
