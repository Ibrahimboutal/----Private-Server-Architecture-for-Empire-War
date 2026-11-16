# Local evidence map

All paths below are relative to this repository. The implementation derives
transport and client calculations from supplied files. New auth, persistence,
limits and error codes are explicitly local research policies.

| Claim | Primary local evidence | Interpretation |
| --- | --- | --- |
| Header/mode/transform | `game_source/_analysis_reports/ghidra/protocol-update-lua.c.txt`, export lines 408810–408942, 408953–409177, 409351–409368 | XOR first, gzip after XOR if length >4096; fixed key resets per message |
| XOR constant | `new-set-up/game_protocol.py:13`; `game_source/empire_proxy/codec.py`; native export encode | `SLGFORFEELINGTECH`, all 17 bytes; obfuscation constant, not account secret |
| Finish-now cost | `GhidraProjects/reports/gold_logic_focus.c:14` | Signed int64 argument; normal defined domain faithfully ported |
| Draft time cost | Same file, line 51 | Seconds, piecewise truncation then rounding |
| School cost | Same file, line 93 | Cap begins at 500020, division by 20 truncates |
| Resource/people cost | Same file, line 116 | Float32 divisions, stored exponent `0.3333333432674408`, ordered double expression |
| Numeric corroboration | `game_source/_analysis_reports/ghidra-economy-audit.md:9–121` | Constant bytes, float32 interpretation, independently recorded boundary vectors |
| City fields | `GhidraProjects/reports/gold_logic_focus.c:208–309` | 32 fields; `foodArmyUseRate` is read as integer then stored as float; `lastupdate` int64 |
| Wire-only extension | `new-set-up/game_protocol_v2.db`, successful login/sync | Additional integer `vacation` preserved |
| Troop base costs | Same DB, 28 successful login rows listed in `data/evidence_manifest.json` | 99 SID entries agree across observed configs; no server modifiers inferred |
| Successful draft envelope | Same DB, rows 14481,14491,14526,14536,28451,28655 | `armys`, `chiefHero`, `currBuilding`, `nextBuilding`, `queues`; no explicit cost table |
| Insufficient resource code | Same DB, draft rows 14927,15359,15384,15415,17288,21715 | `ret=13003`; complete server validation rules still unknown |
| Login/sync containers | Same DB; first successful login row 4 | Login: `memCityResource`; sync: `cityRes`; namespaces `User` / `City` |
| Request sessions | Same DB, observed login/sync requests | No required UUID in these requests; connection binding is used, local UUID stored in backend |

The transport audit's large export line numbers refer to its original export,
not the shortened `.c.txt` file's physical line numbers. Focused TimeUtil line
references above refer to the supplied `gold_logic_focus.c` directly.

`game_protocol_v2.db` inspected during development contained 31,560 rows, all
without raw capture. The older `game_protocol.db` stores some usable ciphertext
bodies and some truncated bodies. Protocol tests compare 100 complete bodies
with their recorded JSON in memory and never persist those packets. Synthetic
full-frame golden vectors separately check headers and XOR key cycling.

The reported market crash and causal float-to-int explanation are research
hypotheses supplied in the request, not facts established by these client exports.
The captures also include a `sellToUser` response at row 25480 with `ret=19011`
and a price-range rejection. That shows at least one rejected price; it does
not prove or disprove the reported process-wide crash under other conditions.
No crash was induced, no official host was contacted, and the Phase 1 router
returns an unimplemented-action error for market operations.

Before later gameplay phases, establish queue completion/modifiers, building
cost tables and prerequisites, reward eligibility/expiry semantics, and complete
client startup requirements from additional decoded fields and controlled local
fixtures. A client calculation or observed response alone does not prove all
server-authoritative logic.
