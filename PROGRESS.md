# PROGRESS.md — Session-to-session state

> Read this first when resuming work in a new Claude Code session. Keep it current: milestone status, decisions,
> known bugs, measured results, and the exact next step.

## Current status

| Milestone | Status | Date | Notes |
|---|---|---|---|
| **M0** Architecture & feasibility | ✅ **Approved by operator** | 2026-10-18 | PLAN.md, RESEARCH.md, ACCEPTANCE.md, provider registry draft, evidence |
| **M1** Infrastructure & data collection | 🟡 **implemented & tested in the cloud; awaiting laptop runs (M1-02, M1-18)** | 2026-10-18 | see results table below |
| M2 Discovery & on-chain safety | ⏸ | — | operator will create free Jupiter key + keyed RPC (Q2 = yes) |
| M3 Features & strategy engine | ⏸ | — | |
| ~~M4 AI judge~~ | ❌ **Dropped** | 2026-10-18 | Operator declined paid API; desk is deterministic-only (PLAN §15) |
| M5 Full PAPER trading | ⏸ | — | |
| M6 Statistical learning & confidence | ⏸ | — | |
| M7 Dashboard & operations | ⏸ | — | |
| M8 LIVE infrastructure | ⏸ | — | no real funds ever used in M8 |
| M9 Forward validation | ⏸ | — | ≥ 60 days calendar time |

**Mode of the system:** nothing runs yet. No keys, no wallet. Paper capital will be a simulated **$100**.

## M0 acceptance results (2026-10-18)

| ID | Result |
|---|---|
| M0-01 | Delivered; pending operator review |
| M0-02 | All external facts in RESEARCH.md carry evidence tags |
| M0-03 | 20 sanitized evidence files in `research/evidence/2026-10-18/` (cookies stripped; secret-pattern scan: only a false positive inside a token's marketing text) |
| M0-04 | Questions batched in PLAN.md §24 |
| M0-05 | No implementation code written |

## M1 acceptance results (2026-10-18, cloud container — see "Where" column)

Commands: `uv run pytest` (offline suite), `uv run pytest -m live` (read-only network), `uv run ruff check src tests`,
`uv run mypy`, `uv run desk doctor`, `uv run desk collect --minutes 60`.
Totals at commit time: **136 offline tests passed, 4 live tests passed, ruff clean, mypy --strict clean (46 files).**

| ID | Result | Where | Evidence |
|---|---|---|---|
| M1-01 Reproducible install | ✅ PASS | cloud (fresh `git clone` + `uv sync --frozen`) | 289 hashes in `uv.lock`; `desk 0.1.0`; same config snapshot `52014c14933b`; 135/135 tests in the clean clone |
| M1-02 `desk doctor` on the laptop | ✅ **PASS** (2026-10-19, operator's laptop) | **15 ok / 3 warn / 0 fail** | see "Operator laptop doctor" below |
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
| M1-18 60-min collection | ⏳ cloud run in progress; **laptop run PENDING — operator** | — | filled in below when complete |
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

### Defects found and fixed during M1 (honest log)
1. **Out-of-range epoch timestamps** raised a raw `ValueError` instead of the typed `TimestampError` — caught by a unit test; fixed.
2. **All providers shared one rate-limit bucket** (every endpoint used the bucket name `default`), so a GeckoTerminal
   429 backoff would also have stalled DexScreener/Jupiter/RPC. Found by inspecting `runtime.db` during the first live
   60-minute run (which was stopped and restarted on the fixed code). Regression test added and shown to fail without
   the fix.
3. **Rate-limiter wait timeout** surfaced as a bare `TimeoutError` instead of `ProviderError(RATE_LIMITED)` — fixed + test.
4. **Response cache kept expired entries** until 2,048 entries (up to ~60 MB on long runs) — now dropped promptly + test.
5. My own analysis error: I reported GeckoTerminal `pools/multi` as returning "3 of 4" pools; the request actually
   contained a duplicate address. Corrected in the registry and RESEARCH.md.

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
| Q2 Free keys | **Yes** — will create a free Jupiter API key and a free keyed RPC (e.g. Helius) | 2026-10-18 | Setup steps provided at end of M1; keys live only in `~/.config/desk/secrets.env` on the laptop |
| Q3 Capital | **$100** | 2026-10-18 | PLAN §13.2 rewritten as a $100 small-account profile: 10% tickets (~$10), max 2 positions, fee floor; paper simulates $100 |
| Q4 AI judge | **No** — "the AI shouldn't judge; I'm not paying for an API" | 2026-10-18 | M4 dropped; PLAN §15 rewritten; M1-20, M6-13, M6-14 added; running cost $0/month |
| Q5 Dashboard stack | **Approved** (React + TypeScript) | 2026-10-18 | — |
| Q6 Brand | **KESTREL** (inferred: "5 approve" covered the summary item that bundled stack + brand) | 2026-10-18 | changeable before M7 |
| Q8 Thresholds | Approved as part of plan approval | 2026-10-18 | research baseline |
| Q7 Telegram, Q9 uptime/wired | not answered — defaults apply | — | non-blocking |

## Next step
1. (Done) Operator approved the plan.
2. M1 in progress — `pyproject.toml` + `uv.lock`, `src/desk/core`, `contracts`, `storage`, provider adapters
   seeded with the M0 fixtures, rate limiter, `desk doctor`. Then run M1 acceptance tests and report actual results here.

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
