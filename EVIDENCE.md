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
| Successful draft envelope | Same DB, rows 14481,14491,14526,14536,28451,28655 | `armys`, `chiefHero`, `currBuilding`, `nextBuilding`, `queues`. Individual `armys` entries include `*Need` and `draftTime` quotes. |
| Insufficient resource code | Same DB, draft rows 14927,15359,15384,15415,17288,21715 | `ret=13003`; complete server validation rules still unknown |
| Login/sync containers | Same DB; first successful login row 4 | Login: `memCityResource`; sync: `cityRes`; namespaces `User` / `City` |
| Request sessions | Same DB, observed login/sync requests | No required UUID in these requests; connection binding is used, local UUID stored in backend |

The transport audit's large export line numbers refer to its original export,
not the shortened `.c.txt` file's physical line numbers. Focused TimeUtil line
references above refer to the supplied `gold_logic_focus.c` directly.

## Phase 2 capture-derived scenario

`empire_war_private_server/data/phase2_scenario.json` bundles a barracks
snapshot, House quotes and a separate Quarry shortage case. They came from
different capture events. The server treats them as fixed quotes for synthetic
scenarios. Its queue scheduler,
synthetic IDs, starting resources and premium money are local research choices.

| Observation | Evidence | Use in this scenario |
| --- | --- | --- |
| Supported soldiers | Rows 4 and 14481, `cfgSoldier` and `armys` | SID 1 Peasant and SID 2 Auxiliary both have a level 1 Barracks prerequisite. Explicit `*Need` fields determine costs. |
| Count and slot limits | Row 4 `cfgBarracksLimitList`, row 14485 `soldierQueue`, row 14491 `queues` | Maximum 50 soldiers per batch. Two queue slots for this barracks profile. |
| Draft duration | Row 14481 and `GhidraProjects/game_logic_output.c:831058` (`DraftArmyDialog::refreshTimeLabel`) | Float32 count × float32 quoted `draftTime`, then truncation to seconds. SID 2 at count 50 yields 1,086 seconds. |
| Queue states and times | Rows 14481 and 14491, `MemBarrackQueue::decode` at `game_logic_output.c:639394` | Active state 1 carries start and end in Unix seconds. Pending state 0 carries zero start/end and the duration in `originalEndtime`. |
| Queue queries | `User/getAllQueue` row 14836, `User/checkAQueue` rows 14932 and 14937, `Building/getBuildingState` row 14485 | Draft completion uses `queueType=1`. The queue overview and building-state response have different field spellings. `DataManager::refreshQueues` at `game_logic_output.c:309758` decodes `buildingQueue` members as `MemBuilding` and `soldierQueue` members as `MemBarrackQueue`. |
| Building coordinates and IDs | `MemBuilding::decode` at `game_logic_output.c:634854`, `BuildingInfoPanel` request construction at line 891804 | `xy = 100*inner + 10*x + y`. The upgrade request's `buildingId` identifies a building type. `getBuildingInfo.uniqueId` identifies the instance. |
| House price and duration | `Building/getBuildingInfo` row 24882 `nextBuilding`, `Building/upgrade` row 24885 | Level 1→2 takes 200 food, 1,000 wood, 100 iron and 200 rock. The quote lists zero gold and people costs, an empty prerequisite list and 60 seconds. |
| Timed building completion | `User/checkAQueue` rows 24895 and 24904 | Type 3 uses the instance ID. An unfinished upgrade returns `needTry=true`. Completion returns level 2, state 0 and `needTry=false`. |
| Instant fee and currency | `game_logic_output.c:891667` composes the UI quote. `BuildingHandler` at line 169939 applies `goldCost`, and `MemUser::decode` at line 637021 maps the relevant balance to `money`. House info row 26546 and instant response row 26551 give a concrete comparison. | For a fully funded House quote with no prerequisites, `timeToFinishNowGold(60)` gives 1 premium money. Row 26551 returns the upgraded level with the prior building timestamps. The emulator ignores `clientMoneyCost`. |
| House population | Row 24882 describes capacities of 100 and 300 at levels 1 and 2. Compare rows 24887 and 24908, and the immediate response at 26551 with sync 26554. | The transition adds 200 to `peopleMax` and 180 to `peopleStable`. Capture order places both increases in the subsequent sync response. |
| Quarry wood shortage | Resource sync row 14607, Quarry info 14611, request 14613, immediate response 14617, and post-upgrade info 14621 | `captured-quarry-shortage-v1` isolates the observed 822 whole wood shortage for an outside Quarry 1→2. The level 2 quote needs 1,000 wood, 360 food, 300 rock and 800 iron. The synthetic setup begins with 178 wood, covers the other costs and charges the captured `goldCost` of 6. |
| Quarry post-upgrade fields | Immediate response row 14617 and resource sync row 14620 | `peopleWorking` increases by 40, `rockAdd` by 266 and `rockMax` by 20,000. The emulator applies these fixed deltas once. |
| Level 3 previews | House info row 26555 and Quarry info row 14621 | `getBuildingInfo` supplies level 3 quote information after the supported level 2 transition. |
| Failed building quotes | Normal `upgrade` rows 2794 and 25878, immediate response row 18836 | The normal insufficient-resources response carries `10004`. The premium shortfall response carries `900001`. |
| Wire time units | 28 successful login responses and 2,811 sync responses with `cityRes` in `game_protocol_v2.db` | `gameTime`, `serverTime` and resource `lastupdate` carry Unix seconds. This corrects the Phase 1 implementation's millisecond assumption. Local `audit.economy_events.created_ms` records milliseconds by design. |

The supported timing values are explicit for these snapshots. For example,
row 14481 quotes SID 2 at approximately 21.73913 seconds per unit while its
base `cfgSoldier.timeNeed` is 30. Other captures contain different durations.
The complete bonus formula, production and population effects for other upgrade
transitions, and general premium conversion for resource shortages need separate evidence.

`game_protocol_v2.db` inspected during development contained 31,560 rows, all
without raw capture. The older `game_protocol.db` stores some usable ciphertext
bodies and some truncated bodies. Protocol tests compare 100 complete bodies
with their recorded JSON in memory and never persist those packets. Synthetic
full-frame golden vectors separately check headers and XOR key cycling.

## Phase 3 market fixture and arithmetic research

`data/market_scenario.json` is generated by `tools/extract_market_profile.py`
using a read-only capture connection. It retains the fixed one-Food quote and
wire shapes; identities, union IDs, distance and absolute dates are replaced
with synthetic placeholders. The fixture does not contain captured credentials.

| Observation | Local evidence | Implemented interpretation |
| --- | --- | --- |
| Request fields/types | `GhidraProjects/game_logic_output.c:542594`, `SellToUserRequest::encode`; all 21 captured requests | `cityId`, `resType`, `resCount`, `unionSel` are int32; `gold` and `hour` are float32 widened to JSON numbers. Namespace is `Market`. |
| Total asking price | `TradeUserSellPage::sellToUser` at line 948550; requests/responses 14703/14705 and 19126/19127 | The client writes `gold = float32(count * unitPrice)`. It is already the listing total, not a unit price. |
| Selected successful request | Row 19126 | Food type 0, count 1, gold `0.119999997317791`, hour 12, union selection 0. Matching uses float32 normalization, so JSON `0.12` is equivalent. |
| Successful response | Row 19127; `SellToUserRespons::decode` at line 542651 and `MemSellTrade::decode` at line 649359 | Response container is `trade`. It has 15 observed fields; idle state/endtime/tooLong are zero, price is 0.12, and `limittime=43200` is a duration. Synthetic IDs/time/distance replace captured values. |
| Isolated resource effect | Resource snapshots 19104→19119 and 19119→19132 | Both intervals span 20 seconds with equal passive changes. The interval containing the listing has one additional Food deducted and no additional change to any other resource field. The extractor checks all fields with absolute tolerance `1e-7`; the local fixture debits one Food only. |
| Own listing query | Request/response 19134/19135; corroborating 14710/14711 | Request is `Market/doGetCityTrade` with `cityId`. Response uses `tradingList`; its 16-field entries omit `tooLong` and add zero `originalEndtime` and `starttime`. |
| Market count refresh | `MarketHandler::onMessage` at line 209615; building-state rows 14709 and 19133 | The client requests building state and resource sync after selling. Observed `market.myCount=1`; the emulator reports its retained listing count. |
| Informational market ranges | Rows 19112 and 14698 | `getUserSellData` returns `citySellData`, containing `countLimit=100000` plus price/min/max for Food, Wood, Iron and Rock. These fields are informational; only the selected one-Food mutation is enabled. |
| Insufficient resources | Food rows 15719/15726 and Wood rows 15740/15742 | Captured codes are 19004 and 19005 respectively. Only Food is part of the enabled listing fixture. |
| Other observed failures | Row 19236, rows 15681/15686, row 25480 | Codes 19008 (quantity), 19009 (caravans) and 19011 (price range). The quantity and price failures have no corresponding captured request, so exact thresholds cannot be recovered from them. |

The Phase 3 native references above are physical lines in the local
`GhidraProjects/game_logic_output.c`; other export/index files use different
offsets. The source JSON records those references and the selected capture rows.

The supplied `(gold * hour) * res_count` overflow model is implemented separately
in `vuln_research.py` using checked binary64 operations and signed int32 bounds.
It is not derived as the official server's economic formula. The reported crash,
its proposed cause and a standalone gold threshold remain unverified by these
client exports. Captured `ret=19011` establishes a price rejection, not the
presence or absence of a process-wide crash under other conditions.

Simulation is a local policy: after a committed audit event, the gateway waits
at most 1,000 ms asynchronously and closes the requesting connection. The Nth
consecutive overflow latches a persistent global breaker; later requests use
safe responses, including after restart. Authentication and city ownership
precede arithmetic. No process crash or event-loop hang is induced, and no
official host is contacted. `audit.market_events` records redacted decoded
requests and framing metadata, not original ciphertext.

The 128-listing storage bound, synthetic zero distance, breaker behavior and
expiry refusal are explicitly local policies. General caravan limits, fees,
buying, cancellation, refunds and expiration settlement have not been inferred.
An own-list query after a retained listing's 12-hour lifetime returns the local
unsupported-scenario code rather than inventing a transition.

Before broadening the fixed scenario, establish wider timing modifiers, building
cost tables and prerequisites, reward eligibility/expiry semantics, and complete
client startup requirements from additional decoded fields and controlled local
fixtures. A client calculation or observed response alone does not prove all
server-authoritative logic.
