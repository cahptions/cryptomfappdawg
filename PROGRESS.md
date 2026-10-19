# PROGRESS.md — Session-to-session state

> Read this first when resuming work in a new Claude Code session. Keep it current: milestone status, decisions,
> known bugs, measured results, and the exact next step.

## Current status

| Milestone | Status | Date | Notes |
|---|---|---|---|
| **M0** Architecture & feasibility | ✅ **Approved by operator** | 2026-10-18 | PLAN.md, RESEARCH.md, ACCEPTANCE.md, provider registry draft, evidence |
| **M1** Infrastructure & data collection | ✅ **PASS** | 2026-10-18 Eastern / 2026-10-19 UTC | laptop doctor and 60-minute collection reported; 139 offline tests, ruff and strict mypy rechecked on resumption |
| M2 Discovery & on-chain safety | 🟡 **implemented; 24-hour acceptance pending** | 2026-10-18 Eastern / Oct 19 UTC | automated checks and short cloud discovery runs passed; see M2 results and M2_HANDOFF.md |
| M3 Features & strategy engine | ⏸ | — | |
| ~~M4 AI judge~~ | ❌ **Dropped** | 2026-10-18 | Operator declined paid API; desk is deterministic-only (PLAN §15) |
| M5 Full PAPER trading | ⏸ | — | |
| M6 Statistical learning & confidence | ⏸ | — | |
| M7 Dashboard & operations | ⏸ | — | |
| M8 LIVE infrastructure | ⏸ | — | no real funds ever used in M8 |
| M9 Forward validation | ⏸ | — | ≥ 60 days calendar time |

**Mode of the system:** read-only collection has run. Free provider keys are configured on the operator's laptop;
they are not stored in this repository. There is no wallet or trading implementation yet. Paper capital will be a
simulated **$100**.

## M0 acceptance results (2026-10-18)

| ID | Result |
|---|---|
| M0-01 | Delivered; operator approved 2026-10-18 |
| M0-02 | All external facts in RESEARCH.md carry evidence tags |
| M0-03 | 20 sanitized evidence files in `research/evidence/2026-10-18/` (cookies stripped; secret-pattern scan: only a false positive inside a token's marketing text) |
| M0-04 | Questions batched in PLAN.md §24 |
| M0-05 | No implementation code written |

## M1 acceptance results (2026-10-18, cloud container — see "Where" column)

Commands: `uv run pytest` (offline suite), `uv run pytest -m live` (read-only network), `uv run ruff check src tests`,
`uv run mypy`, `uv run desk doctor`, `uv run desk collect --minutes 60`.
Totals at commit time: **139 offline tests passed, 4 live tests passed, ruff clean, mypy --strict clean (46 files).**

| ID | Result | Where | Evidence |
|---|---|---|---|
| M1-01 Reproducible install | ✅ PASS | cloud (fresh `git clone` + `uv sync --frozen`) | 289 hashes in `uv.lock`; `desk 0.1.0`; same config snapshot `52014c14933b`; 135/135 tests in the clean clone |
| M1-02 `desk doctor` on the laptop | ✅ **PASS** (2026-10-19 UTC, operator's laptop) | keyed rerun: **19 ok / 1 warn / 0 fail** | see "Operator laptop doctor" below |
| M1-03 Config hash | ✅ PASS | unit | `tests/unit/test_config.py` |
| M1-04 Ceilings | ✅ PASS | unit | ceiling + floor + paid-provider tests |
| M1-05 Real fixtures parse | ✅ PASS | unit | 15 real responses, 0 validation errors |
| M1-06 Unknown is never zero | ✅ PASS | unit + DB | DexScreener bonding-curve liquidity → MISSING; DB CHECK constraint |
| M1-07 Schema drift rejected | ✅ PASS | unit + mocked HTTP | truncated JSON, wrong shapes, NaN, per-item errors, typed `SCHEMA` errors recorded in health |
| M1-08 Units | ✅ PASS | property tests | decimals 0–12, lamports, 70-digit provider decimals |
| M1-09 Timestamps | ✅ PASS | unit | ISO `Z`, offsets, epoch s/ms, nanoseconds, s/ms mix-ups rejected |
| M1-10 Budget never exceeded | ✅ PASS | simulated 10 min × 3 tasks + **3 real OS processes** | no window ever over budget |
| M1-11 Backoff on 429 | ✅ PASS | unit + mocked HTTP | `retry-after: 0` ignored; 2→4→8 s; capped; per-provider isolation |
| M1-12 Cache + single-flight | ✅ PASS | unit + mocked HTTP | 10 concurrent → 1 upstream call; expired entries dropped |
| M1-13 Databases | ✅ PASS | unit + fault injection | 4 DBs from empty, WAL, integrity ok, migration tamper detection, **kill -9 lost 0 committed rows** |
| M1-14 Secret redaction | ✅ PASS | unit | keys absent from message/args/extra/exception text and JSON log file |
| M1-15 Provider health | ✅ PASS | unit | OK→DEGRADED(3)→DOWN(10)→OK(3 successes), transitions timestamped |
| M1-16 Provider registry | ✅ PASS | unit | `config/providers.toml` schema, buckets, evidence files, every adapter has an entry |
| M1-17 Raw evidence | ✅ PASS | unit | byte-identical by SHA-256; corruption detected; retention keeps decision-linked |
| M1-18 60-min collection | ✅ **PASS — laptop results supplied by operator** | laptop 2026-10-19 00:01–01:01 UTC (Oct 18, 8:01–9:01 p.m. Eastern) | see "Operator laptop 60-minute run" below; previous cloud evidence retained separately |
| M1-19 On-chain constants | ✅ PASS | live (cloud) | Token, Token-2022, wSOL, USDC, venue programs vs live pools |
| M1-20 No paid / LLM deps | ✅ PASS | unit | no LLM SDK in lockfile, no LLM hosts in source, all providers $0 |

### Operator laptop doctor (M1-02, 2026-10-19 ~00:00 UTC)
Same config snapshot as tested (`52014c14933b`) → the laptop runs the exact verified configuration.

| Check | Laptop result |
|---|---|
| Python | 3.12.3 (system, Mint 22.3) |
| Disk | 274 GB free for `~/desk-data` |
| Memory / swap | 9.6 GB available of 15.0 GB; **swap 1456/2047 MB (71%) — warning** |
| NTP / clock vs chain | synchronized / +1.7 s |
| Power | BAT1 full, 100% |
| Keys | none yet (keyless mode) |
| GeckoTerminal | 20 new pools, 241 ms, 0 schema problems |
| DexScreener | 148 ms |
| Jupiter quote | 0.1 SOL → 10.4002 USDC (SOL ≈ $104.00), metis, 2 bps, 317 ms |
| Jupiter tokens (keyless lite-api) | 2/2 in 181 ms |
| Solana public RPC | slot 458626262 in 401 ms; Token-2022 mint parsed in 117 ms |
| **Public RPC `getTokenLargestAccounts`** | **429 rate_limited from the home IP too** → R-01 resolved: a keyed RPC is REQUIRED for holder analysis (M2) |

Laptop latencies are lower than the cloud container's on every provider.

**Doctor re-run with the free keys configured (2026-10-19): 19 ok / 1 warn (swap 64%) / 0 fail.**
Secrets file private (0600); Jupiter key and Helius RPC URL configured (values never recorded). Jupiter tokens via
`api.jup.ag` (keyed) 2/2 in 41 ms; keyed RPC slot in 155 ms; Token-2022 mint in 38 ms;
**`getTokenLargestAccounts` returned 20 accounts in 85 ms** — holder analysis for M2 is unblocked. Market migration
0002 applied automatically on the laptop database.

### Operator laptop 60-minute run (M1-18)

Evidence source: the operator pasted the terminal summary in this chat. This is an operator-reported laptop run,
not a new cloud execution. Run ID: `run_01M58QDRAS0EG93KBSJYMDSG4E`.
Started `2026-10-19T00:01:47.865649+00:00`; ended `2026-10-19T01:01:47.489277+00:00`.
Local time: **October 18, 8:01–9:01 p.m. America/New_York**.

| Job | Polls | OK | Failed | Success | Items | Item errors |
|---|---|---|---|---|---|---|
| geckoterminal_new_pools | 60 | 60 | 0 | 100% | 2,400 | 0 |
| geckoterminal_trending | 30 | 30 | 0 | 100% | 600 | 0 |
| jupiter_recent | 60 | 60 | 0 | 100% | 1,800 | 0 |
| jupiter_trending | 20 | 20 | 0 | 100% | 1,000 | 0 |
| dexscreener_refresh | 60 | 60 | 0 | 100% | 9,494 | 0 |
| rpc_slot | 60 | 60 | 0 | 100% | 60 | 0 |

Unhandled exceptions **0**; suspend gaps **0**; tracked tokens **261**; memory **50.9 MB current and peak**.
Reported database totals: tokens **1,159**, pools **1,215**, market snapshots **12,494**, token claims **2,800**,
quotes **0**, mint accounts **0**, raw responses **601**, ingest errors **0**. These are database totals, not
asserted run-only deltas. Zero quote/mint rows are expected for M1 collection and do not validate M2 security or
execution. M1-18's poll-success, exception and <500 MB memory thresholds are met by the supplied report.

### Cloud 60-minute run (M1-18, cloud container, 2026-10-18 23:03 → 2026-10-19 00:03 UTC)
| Job | Polls | OK | Failed | Success | Items | Item errors |
|---|---|---|---|---|---|---|
| geckoterminal_new_pools | 60 | 60 | 0 | 100% | 2,400 | 0 |
| geckoterminal_trending | 30 | 30 | 0 | 100% | 600 | 0 |
| jupiter_recent | 60 | 60 | 0 | 100% | 1,800 | 0 |
| jupiter_trending | 20 | 20 | 0 | 100% | 1,000 | 0 |
| dexscreener_refresh | 60 | 60 | 0 | 100% | 10,250 | 0 |
| rpc_slot | 60 | 60 | 0 | 100% | 60 | 0 |

Unhandled exceptions **0** · suspend gaps 0 · tracked tokens 269 · **memory 59.5 MB (peak)** (run used the cache code
from before the eviction fix) · rows: 13,250 snapshots, 1,139 tokens, 1,185 pools, 2,800 token claims, 617 raw
responses · disk: 23 MB DB + 6.9 MB raw per hour · `integrity_check = ok`.
24 HTTP 429s were absorbed by backoff; health transitions recorded and self-recovered: GeckoTerminal
ok→degraded→ok (23:33→23:37), Jupiter ok→degraded→ok (23:58→00:00).

**Finding from the run — drained pools:** 602 snapshots (22 PumpSwap pools among ~269 tracked tokens) had
DexScreener `liquidity.usd = 0` *explicitly*, with reserves like 34 tokens / 0.0000217 SOL while 24 h volume was still
shown (~$9k). These are drained pools (a rug signature). The values were stored truthfully but unflagged, unlike
GeckoTerminal zeros. Fixed: both providers now flag `ZERO_REPORTED` / `ZERO_LIQUIDITY_REPORTED`, and DexScreener
reserve amounts are stored (`market` migration 0002). Real response saved as evidence + fixture
(`research/evidence/2026-10-19/m1_dexscreener_tokens_v1_drained_pumpswap_pools.json`). Input for M2 rug detection.

### Defects found and fixed during M1 (honest log)
1. **Out-of-range epoch timestamps** raised a raw `ValueError` instead of the typed `TimestampError` — caught by a unit test; fixed.
2. **All providers shared one rate-limit bucket** (every endpoint used the bucket name `default`), so a GeckoTerminal
   429 backoff would also have stalled DexScreener/Jupiter/RPC. Found by inspecting `runtime.db` during the first live
   60-minute run (which was stopped and restarted on the fixed code). Regression test added and shown to fail without
   the fix.
3. **Rate-limiter wait timeout** surfaced as a bare `TimeoutError` instead of `ProviderError(RATE_LIMITED)` — fixed + test.
4. **Response cache kept expired entries** until 2,048 entries (up to ~60 MB on long runs) — now dropped promptly + test.
6. **DexScreener explicit-zero liquidity was not flagged** (GeckoTerminal zeros were) — found in the 60-minute run; fixed,
   reserve amounts now stored (migration 0002), upgrade-from-v1 migration test added.
5. My own analysis error: I reported GeckoTerminal `pools/multi` as returning "3 of 4" pools; the request actually
   contained a duplicate address. Corrected in the registry and RESEARCH.md.

## M2 implementation and acceptance status (2026-10-19 UTC / October 18 Eastern)

Resumed from Claude's latest pushed commit `df4866d`. The Claude session URL was inaccessible from this
environment; the repository's PLAN, ACCEPTANCE and PROGRESS documents supplied the handoff. User instruction:
finish M1 and continue through the remaining milestones. Free keys were already verified on the laptop.

Implemented discovery identities/source observations, independent timestamps, resumable backfill, stages 0–4,
mint/extension policy, conservative PumpSwap/PDA/vault/LP verification, lower-bound holder metrics, Shield and
fresh exact-quantity quote checks, append-only stage evidence, fair bounded queues, request-allocation totals,
and a local read-only console. No wallet, funds, LLM or paid service was used.

| Criteria | Status | Evidence |
|---|---|---|
| M2-01–04 Identity, source dedup, timestamps and reasons | PASS (automated) | tests/unit/test_discovery.py; migration detection and all six reason fixtures; ordinary refresh is not renewed activity |
| M2-05–06 Authorities/extensions | PASS (automated) | tests/unit/test_security.py; real Token-2022 fixture and unsafe/unknown extension cases; missing/empty authority fields fail closed |
| M2-07–09 Holders, attribution and claim separation | PASS (automated; synthetic proof sets) | concentration/exclusion arithmetic, owner aggregation, provider conflict and cluster contract tests; complete live holder resolution still requires the laptop keyed run |
| M2-10–11 Routes, costs and missing evidence | PASS (automated) | stale/mismatched quantity quotes, unavailable routes, cost cap, missing required evidence and Shield tests |
| M2-12–13 Audit completeness/provenance | PASS in tests and short runs; long-run evidence pending | atomic rollback, append-only SQL triggers, zero orphaned candidates in cloud smoke reports; every result has rule/values/thresholds/hash |
| M2-14 Actual 24-hour discovery | **PENDING** | 2-minute / 2-minute / 1-minute cloud runs are smoke tests only; the duration gate correctly refuses the 1-minute run |
| M2-15 Scores cannot override security | PASS (automated) | score is not accepted as a policy input; storage refuses security PASS without a matching assessment |

Validation: **212 offline tests passed**, **4 live provider tests passed**, Ruff clean, strict mypy clean
(58 source files), frozen dependency install, wheel build, database integrity/WAL checks, and functional startup
of the CLI console with an HTTP status request. Offline tests include real SQLite/HTTP boundaries with mocked
provider responses; they do not substitute for live keyed safety evidence.

Actual cloud discovery runs are saved in `research/evidence/2026-10-19/m2_cloud_smoke_runs.json`. The latter
two runs reported **57.3 MB** and **57.6 MB** peak memory, zero unhandled exceptions, zero ingest errors and
zero orphaned candidates. Final cumulative candidate count: **573**. Source and outcome totals are cumulative;
the final report also separates `during_run` events. These are **cloud**, not laptop, measurements.

Public RPC holder queries hit HTTP 429 during the first run; candidates stayed DEFER, never PASS. Later
iterations rejected known unsupported pool features before spending further holder/route requests. A real
PumpSwap account and a real Shield response were saved byte-for-byte alongside provenance records. Current
special PumpSwap features and all unverified venues remain excluded; see M2_HANDOFF.md for exact limits.

Cloud setup draft saved: corrected repository SHA/mount path, tested frozen install script, startup instructions
and the six required provider domains. Draft persistence is confirmed; review/save/publication and fresh-task
restoration have **not** been claimed. The draft does not contain credential values.

## Key measurements from M0 (details + evidence in RESEARCH.md)

- Jupiter quote round-trip cost on memecoins with $22k–$59k liquidity: **2.2%–4.1%** (0.1–1 SOL tickets).
- Jupiter platform fee: 10 bps/side on memecoins; 2 bps SOL→USDC (RFQ).
- GeckoTerminal: documented ~10 calls/min; measured ~50% 429s at ~35 attempted/min; 60 s cache.
- DexScreener: 300/min on pair/token endpoints, 30 addresses/call; `liquidity` absent on bonding-curve pairs.
- Public RPC: `getTokenLargestAccounts` 429 on 4/4 attempts (shared cloud IP — re-measure at home in M1).
- Public WS: pump.fun program logs ≈ 140–234 msg/s ≈ 19 GB/day.
- New pump.fun mints: Token-2022 with `metadataPointer` + `tokenMetadata` only.
- Token-account rent: 0.001488 SOL (165 B), 0.001514 SOL (170 B).
- SOL ≈ $101.8 at research time.

## Architectural decisions (proposed in M0; final once approved)

| ID | Decision | Rationale |
|---|---|---|
| D-01 | Four processes: engine, guardian, console, research (+ signer only in LIVE) | Failure-domain isolation; guardian owns all exits |
| D-02 | Three SQLite DBs: `market.db`, `ledger-paper.db` / `ledger-live.db`, `research.db` | Physical mode separation; small critical ledger backed up often |
| D-03 | Horizon 15 min–24 h; no sniping | Data cached 30–60 s; costs 2–4% |
| D-04 | DexScreener = refresh workhorse; GT = discovery + OHLCV; Jupiter = quotes + Tokens v2 + execution | Measured/documented limits |
| D-05 | Token-2022 extension allow-list (fail-closed) | pump.fun mints are Token-2022 |
| D-06 | `Observed[T]` wrapper; unknown never zero | Spec + DexScreener missing-field finding |
| D-07 | Pure decision core shared by SHADOW/PAPER/LIVE | Replay determinism; mode parity |
| D-08 | Conservative paper profile drives portfolio state | Avoid optimistic simulation |
| D-09 | **No AI judge, no LLM, no paid APIs** (operator decision 2026-10-18). "Models evaluate" = local statistical models in M6, zero influence until validated | Operator declined paying for an API; spec permits deterministic-only mode |
| D-10 | LIVE arming is CLI-only; dashboard can only disarm | Attack surface |
| D-11 | React + TS + Vite dashboard; UI built with Node or Docker; runtime Python-only | Polish + maintainability |
| D-12 | Internal package `desk`, brand only in UI (working brand KESTREL) | Brand can change freely |
| D-13 | Python 3.12 via `uv`, hash-locked | Matches operator's Mint 22.3; reproducible |
| D-14 | Address constants verified on-chain, never typed from memory | A memory-typed USDC mint was wrong during M0 |
| D-15 | Minimal dependencies: runtime M1 deps are only `pydantic` + `httpx`; stdlib `sqlite3` + numbered SQL migrations (not SQLAlchemy/Alembic), stdlib `logging` JSON formatter (not structlog), `tomllib` registry `config/providers.toml` (not YAML), `argparse` CLI | Fewer moving parts for a single beginner maintainer; plain SQL is readable |
| D-16 | Fourth SQLite file `runtime.db` for cross-process coordination (rate-limit windows, provider health, heartbeats) | Keeps transient coordination writes away from the market store and the money ledger |
| D-17 | $100 small-account sizing profile (10% tickets, max 2 positions, fee floor) | 2% tickets would be ~$2, where fees could be 0.25–5% per trade |

## Known issues / open items
See RESEARCH.md §11 (R-01 … R-10) and PLAN.md §23 (risk register).

## Operator environment (from operator, 2026-10-18)
Linux Mint 22.3 (Ubuntu 24.04), Ryzen 7 7730U, 16 GB RAM (swap 83% used at report time, ~6 Docker containers),
~290 GB free NVMe, Wi-Fi only (RTL8821CE), laptop on battery/AC, Tailscale installed, 1920×1080 display.

## Operator answers received
| Question | Answer | Date | Effect |
|---|---|---|---|
| Q1 Plan | **Approved** | 2026-10-18 | M1 started |
| Q2 Free keys | **Done** — free Jupiter API key + free Helius RPC configured on the laptop | 2026-10-19 | Verified by doctor (keyed Jupiter tokens, keyed RPC, holder list OK); keys live only in `~/.config/desk/secrets.env` |
| Q3 Capital | **$100** | 2026-10-18 | PLAN §13.2 rewritten as a $100 small-account profile: 10% tickets (~$10), max 2 positions, fee floor; paper simulates $100 |
| Q4 AI judge | **No** — "the AI shouldn't judge; I'm not paying for an API" | 2026-10-18 | M4 dropped; PLAN §15 rewritten; M1-20, M6-13, M6-14 added; running cost $0/month |
| Q5 Dashboard stack | **Approved** (React + TypeScript) | 2026-10-18 | — |
| Q6 Brand | **KESTREL** (inferred: "5 approve" covered the summary item that bundled stack + brand) | 2026-10-18 | changeable before M7 |
| Q8 Thresholds | Approved as part of plan approval | 2026-10-18 | research baseline |
| Q7 Telegram, Q9 uptime/wired | not answered — defaults apply | — | non-blocking |

## Next step
1. (Done) Operator approved the plan.
2. M1 complete. On resumption, `uv sync --frozen` succeeded; `uv run pytest`: **139 passed, 4 live tests
   deselected**; `uv run ruff check src tests`: clean; `uv run mypy`: **46 source files clean**. These new checks
   were offline in the Codex cloud machine; previous live and laptop evidence is identified above.
3. Fetch the `codex/m2-discovery` delivery branch into the existing laptop checkout (see `M2_HANDOFF.md`);
   `uv sync --frozen`; keep using the already
   configured free keys. Run `uv run desk discover --minutes 1440`, then
   `uv run desk discovery-report --require-hours 24` and `uv run desk db check`. Record the actual results.
4. Once M2 acceptance is recorded, continue M3, M5, M6, M7, M8 (zero funds), then M9 (real calendar-time
   evidence). M4 remains dropped. Do not claim any of those later milestones complete from M2 smoke tests.

## Session log
- **2026-10-18 (session 1):** M0 research (live read-only probes of GeckoTerminal, DexScreener, Solana public RPC/WS,
  Jupiter v1/v2/Tokens/Shield; docs for Jupiter, Helius, Solana clusters, DexScreener, GeckoTerminal; Anthropic
  reference). Wrote PLAN.md, RESEARCH.md, ACCEPTANCE.md, PROGRESS.md, `research/provider_registry.yaml`, evidence.
  Incorporated operator's hardware report (PLAN §2, §18). Built a static visual concept
  (`docs/design/kestrel-concept.html`, sample data only); validated chart/text colours with the dataviz checker —
  muted text changed `#5F6D7D` → `#76869A` (contrast 3.6 → 5.1:1); brand neons restricted to identity use; series
  palette stepped into the dark band (worst adjacent CVD ΔE 7.9 ⇒ direct labels mandatory). Rendered at 1920×1080 and
  390 px: no console errors, no external requests, no horizontal page scroll. Stopped for approval.
- **2026-10-18 (session 1, later):** Operator answered Q4: no AI judge / no paid API. Removed the judge from PLAN
  (§1, §3.1, §4 diagram and pipeline, §5, §6, §7, §8, §9, §13, §15, §19, §20, §22, §24), dropped M4 in ACCEPTANCE
  (moved its surviving intents to M1-20, M6-13, M6-14, M7-04), marked Anthropic EXCLUDED in the registry and RESEARCH,
  removed the judge from the design concept. Still awaiting Q1 (plan approval).
