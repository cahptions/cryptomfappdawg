# PROGRESS.md — Session-to-session state

> Read this first when resuming work in a new Claude Code session. Keep it current: milestone status, decisions,
> known bugs, measured results, and the exact next step.

## Current status

| Milestone | Status | Date | Notes |
|---|---|---|---|
| **M0** Architecture & feasibility | ✅ **Approved by operator** | 2026-10-18 | PLAN.md, RESEARCH.md, ACCEPTANCE.md, provider registry draft, evidence |
| **M1** Infrastructure & data collection | 🔧 **in progress** | 2026-10-18 | started after approval |
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
