# ACCEPTANCE.md — Objective Acceptance Tests per Milestone

Rules that apply to every milestone:

- Development scheduling (operator clarification, 2026-10-19): later software may be built and tested while
  earlier measured runs are pending. This changes scheduling only; every acceptance criterion and real-duration
  requirement below still applies. A smoke test or synthetic timeline cannot close a measured-run criterion.
- A milestone is **accepted only when every test below passes and the actual output is reported** in PROGRESS.md
  (command, date, pass/fail counts, notable numbers). Untested features are never described as working.
- Tests are never weakened, skipped, or deleted to make a milestone pass. If a criterion turns out to be wrong, the
  change is proposed to the operator with a justification and recorded here with a dated note.
- **Test kinds:** `U` unit (deterministic, mocked I/O) · `P` property-based (`hypothesis`) · `F` fixture-based (real
  sanitized provider responses) · `E2E` end-to-end scenario · `FI` fault injection · `L` opt-in live read-only
  integration (`pytest -m live`, needs network) · `M` manual operator check (scripted checklist) · `R` measured run
  (report numbers).
- The whole suite (`uv run pytest -m "not live"`) must stay green at the end of each milestone, including all earlier
  milestones' tests. `ruff` and `mypy --strict` (on `src/desk/core`, `contracts`, `portfolio`, `risk`, `execution`,
  `desk_signer`) must pass.

---

## M0 — Architecture and feasibility

| ID | Criterion | Kind | Pass condition |
|---|---|---|---|
| M0-01 | PLAN.md, RESEARCH.md, ACCEPTANCE.md, PROGRESS.md exist and cover the spec's requested sections | M | Operator review |
| M0-02 | Every external fact in RESEARCH.md is tagged MEASURED / DOCUMENTED / SECONDARY / ASSUMPTION / TO VERIFY | M | Spot check |
| M0-03 | MEASURED claims have a saved, sanitized raw response in `research/evidence/` | M | Files present; no secrets/cookies |
| M0-04 | Blocking questions are asked in one batch | M | PLAN.md §24 |
| M0-05 | No implementation code written before approval | M | Repo contains docs + evidence + registry draft only |

---

## M1 — Infrastructure and data collection

| ID | Criterion | Kind | Pass condition |
|---|---|---|---|
| M1-01 | Reproducible install | M/R | On a clean clone: `uv sync --frozen` succeeds; `uv run desk --version` prints the version; lockfile contains hashes |
| M1-02 | `desk doctor` on the operator's laptop | M/R | Reports Python, disk, RAM/swap, NTP sync, battery, per-provider latency and rate-limit headers, key presence (never values); output pasted into PROGRESS.md |
| M1-03 | Config: single authoritative definition + snapshot hash | U | Loading `default.toml` twice yields the same hash; changing any threshold changes the hash; unknown keys rejected |
| M1-04 | Safety ceilings cannot be exceeded by strategy config | U | A strategy config value above a ceiling fails validation with a clear error |
| M1-05 | Each adapter parses its stored real fixtures into contracts | F | GT `new_pools`, GT `trending_pools`, DexScreener `tokens/v1`, Jupiter `/order` (v2), Jupiter Tokens v2 `search` and `recent`, RPC `getAccountInfo` (Token-2022 mint) all parse with zero validation errors |
| M1-06 | **Unknown is never zero** | F/U | DexScreener bonding-curve pair without `liquidity` ⇒ `Observed(status=MISSING, value=None)`; GT `reserve_in_usd: null` ⇒ MISSING; GT `"0.0"` ⇒ `OK` + `SUSPICIOUS_ZERO` flag; arithmetic helpers raise on `None` |
| M1-07 | Corrupted / schema-drifted responses are rejected explicitly | FI | Truncated JSON, wrong types, missing required fields ⇒ adapter returns a typed error and health records it; no partial objects stored |
| M1-08 | Units and decimals | P | Raw ↔ UI amount round-trips exactly for decimals 0–12; lamports ↔ SOL exact with `Decimal`; GT high-precision decimal strings parse without float loss |
| M1-09 | Timestamps | U/F | ISO-8601 `Z`, epoch-seconds and epoch-milliseconds all normalise to tz-aware UTC; naive datetimes rejected at the contract boundary |
| M1-10 | Rate limiter never exceeds budget | P/U | Simulated 10-minute load across 3 concurrent tasks: requests per provider ≤ configured budget in every 60 s window; budget state shared across processes via SQLite |
| M1-11 | Adaptive backoff on 429 | FI | Mock returning 429 with `retry-after: 0` ⇒ backoff grows exponentially with jitter (not immediate retry); success resets |
| M1-12 | Cache + single-flight dedup | U | 10 concurrent identical requests within TTL ⇒ exactly 1 upstream call; after TTL ⇒ refetch |
| M1-13 | Databases | U/FI | Migrations build all DBs (market, ledger-paper, research, runtime) from empty; `PRAGMA integrity_check = ok`; WAL enabled; `kill -9` during a write loop loses no committed rows |
| M1-14 | Structured logging with secret redaction | U | Values of env vars/settings matching `*KEY*`, `*SECRET*`, `*TOKEN*`, `*PASSPHRASE*` never appear in log output (asserted on captured logs) |
| M1-15 | Provider health | U | N consecutive failures ⇒ `DEGRADED`, then `DOWN`; recovery after M successes; transitions recorded with timestamps |
| M1-16 | Provider registry | U | `config/providers.toml` validates against its schema; every adapter has an entry; every entry has verified-fields, limits, freshness, cost, fallback |
| M1-17 | Raw evidence | U | Each stored response is retrievable by SHA-256 and decompresses byte-identical |
| M1-18 | **Stable read-only collection** | L/R | 60-minute run on the operator's laptop: zero unhandled exceptions; per provider ≥ 95% of scheduled polls succeed *or* are recorded as degraded with reason; RSS < 500 MB; numbers reported |
| M1-19 | Verified program/mint constants | L | Each hard-coded program ID / mint has a live check asserting its on-chain owner/type (RESEARCH.md §5) |
| M1-20 | **No paid or LLM dependencies** (operator decision 2026-10-18) | U | Lockfile contains no LLM SDK (`anthropic`, `openai`, etc.); no source file references an LLM API host; every provider in `config/providers.yaml` has `cost_usd_month: 0`, and a non-zero entry fails validation |

---

## M2 — Discovery and on-chain safety

| ID | Criterion | Kind | Pass condition |
|---|---|---|---|
| M2-01 | Token and pool identities are independent | U | One mint with 3 pools ⇒ 1 token, 3 pools; a new pool for an existing token is classified `NEW_POOL`/`MIGRATION`, not `NEW_TOKEN` |
| M2-02 | Dedup by canonical mint and pool address | U | The same discovery from 3 sources ⇒ 1 discovery record with 3 source observations |
| M2-03 | Separate timestamps | U | `first_observed_at`, token `created_at`, pool `created_at` stored independently; none overwritten by another |
| M2-04 | Discovery reason classification | U/F | Fixtures for each of the six reasons produce the correct label |
| M2-05 | Mint/freeze authority detection | F/U | Non-null mint authority ⇒ REJECT `MINT_AUTHORITY`; non-null freeze ⇒ REJECT `FREEZE_AUTHORITY` |
| M2-06 | Token-2022 extension policy (fail-closed) | F/U | Real pump.fun mint fixture (`metadataPointer` + `tokenMetadata`) ⇒ PASS; fixtures/synthetic mints with `transferFeeConfig`, `transferHook`, `permanentDelegate`, `defaultAccountState=frozen`, `nonTransferable`, `pausable`, `confidentialTransferMint`, and an unknown extension ⇒ REJECT with the specific reason |
| M2-07 | Holder concentration raw vs adjusted | F/U | Synthetic holder set with a verified pool vault, a verified bonding curve, a burn address and an unverified "infra-looking" account ⇒ only the first three are excluded from adjusted metrics; the unverified one is counted and reported as `unclassified` |
| M2-08 | Attribution never overstated | U | Clustered wallets are reported as `suspected_cluster` with evidence list, never `same_owner` |
| M2-09 | Provider claims kept separate | U | Jupiter `topHoldersPercentage` stored as a provider claim; disagreement with on-chain > 10 pp flagged `CONFLICT` |
| M2-10 | Sell route required | U | No Jupiter sell quote ⇒ REJECT `NO_SELL_ROUTE`; round-trip quote cost above cap ⇒ REJECT `COST_TOO_HIGH` |
| M2-11 | Missing data never passes a hard rule | U | Missing liquidity / missing holders / missing authority data ⇒ `INSUFFICIENT_DATA` reject (fail-closed), never PASS |
| M2-12 | Rejection ledger completeness | U/R | SQL integrity check: every candidate has a terminal stage result or explicit pending state; zero orphans after a 24 h run |
| M2-13 | Every stage result records rule id, observed values, thresholds, config hash | U | Schema test + sample assertion |
| M2-14 | 24 hours of live discovery, collected in pieces (operator decision 2026-10-20) | L/R | `desk discovery-report --require-hours 24` passes: ≥ 24 h of cleanly finished discovery runs within 14 days (active time; sleep excluded), at least one ≥ 4 h, each with no unhandled exceptions, audit integrity and every source job succeeding; report includes discoveries by source and reason, stage counts, rejection reasons, provider budget, RSS, DB growth. `--continuous` keeps the old one-run rule |
| M2-15 | Model scores cannot override security exclusions | U | Injecting a maximal "score" into the candidate does not change a Stage-4 REJECT |

---

## M3 — Feature and strategy engine

| ID | Criterion | Kind | Pass condition |
|---|---|---|---|
| M3-01 | Feature registry completeness | U | Every feature declares definition, unit, source, max age, missing policy, version; registry rejects incomplete entries |
| M3-02 | **No look-ahead** | P/U | Inserting synthetic rows with `observed_at > as_of` changes no feature value at `as_of` |
| M3-03 | Max-age enforcement | U | A feature older than its max age ⇒ `STALE` and follows its missing policy |
| M3-04 | Feature correctness | U | Hand-computed expected values for each feature on a fixed synthetic series match to stated precision |
| M3-05 | Volume-decay variants | U | Token aged 7 h: `VD-orig` and `VD-adj` produce the documented different ratios; 24 h+ token: identical |
| M3-06 | Regime classifier determinism | U | Same inputs ⇒ same label; definition change ⇒ new version id |
| M3-07 | Strategy spec completeness | U | A spec missing any required section (eligibility, features, entry, rejection, sizing, stop, trailing, max hold, liquidity exits, execution requirements, invalidation) fails validation |
| M3-08 | Strategy versioning immutability | U | Modifying a used version raises; a change creates a new version with `parent_version` |
| M3-09 | NO-TRADE always present | U | Ranking output always contains NO-TRADE; with no candidate clearing thresholds, NO-TRADE is chosen; ties go to NO-TRADE |
| M3-10 | Decision determinism | P | Same snapshot + config ⇒ byte-identical decision hash across 1,000 randomized runs |
| M3-11 | Replay reproduces SHADOW decisions | E2E | Replaying a recorded 6-hour SHADOW session reproduces 100% of decisions; changing one threshold in a copy changes only the expected decisions and never alters source data. A run is replayed with the evaluator version that recorded it (v1, v2 or v3); an unknown version fails explicitly |
| M3-12 | Baselines | U | B0 returns no trades; B1 is seeded and reproducible; B2 deterministic |
| M3-13 | Decisions on real builder output | U | Decisions built from the real feature builder (not hand-made contexts) can choose a strategy; every PAPER strategy's production input availability is asserted, so an always-NO_TRADE desk cannot pass unnoticed |

---

## M4 — AI judge — DROPPED (operator decision, 2026-10-18)

The operator declined a paid AI API; the desk is deterministic-only (PLAN.md §15). There are no M4 tests. The parts
of M4 that still matter were moved, not deleted:

| Former intent | Now covered by |
|---|---|
| Model predictions logged and compared with deterministic baselines | M6-13, M6-14 (local statistical models) |
| Model output never sizes, never overrides security or risk | M2-15, M6-14 |
| Hostile token text (names/descriptions) handled safely | M7-04 (XSS / Unicode), M1-07 (corrupted input) |
| No paid API usage | M1-20 |

---

## M5 — Full PAPER trading

| ID | Criterion | Kind | Pass condition |
|---|---|---|---|
| M5-01 | Paper wallet accounting | U/P | Cash + position values (conservative profile) reconcile to the ledger exactly after any sequence of simulated trades |
| M5-02 | Sizing rules | U/P | Respects every cap (fraction, free capital, liquidity, absolute); returns 0 when cost cap, fee floor, or uncertainty rule fails; never uses reserved funds; never adds to losers |
| M5-03 | Fee-floor calculation | U | Table-driven cases: ticket where fixed costs > 1% ⇒ size 0 |
| M5-04 | Quote-based fills | F | Entry uses `/order` `outAmount`; marks use sell quotes for the exact quantity; **no P&L is computed from displayed price** (test asserts the code path) |
| M5-05 | Three profiles | U | Same decision yields observed ≥ conservative ≥ stress net result on fixtures; profile differences stored per trade |
| M5-06 | Quote expiry and latency | U | Quote older than max age ⇒ re-quote or no trade; latency recorded |
| M5-07 | Failure handling | FI | No route at entry ⇒ `ENTRY_FAILED`; failed exit ⇒ retries ⇒ `EXIT_BLOCKED` with critical alert; lock retained; never recorded as closed |
| M5-08 | Liquidity collapse | FI | Pool liquidity −90% fixture ⇒ guardian exit attempt; if no route ⇒ `IMPAIRED`, valued at executable quote or zero |
| M5-09 | Exit policies | U | Each of the 8 exit policies triggers exactly on its boundary cases (table tests) |
| M5-10 | Position locks / duplicate prevention | U/FI | Two concurrent entry attempts on the same mint ⇒ exactly one position; duplicate intent id refused |
| M5-11 | Restart recovery | FI | Kill engine and guardian with an open position and a pending exit; restart ⇒ identical position state, limits, halts and daily-loss counters; exit resumes |
| M5-12 | Guardian independence | FI | Engine killed ⇒ guardian keeps marking and exiting; entries impossible while guardian heartbeat stale |
| M5-13 | Kill switch | E2E | Creating STOP ⇒ no new entry within ≤ 5 s; open positions remain supervised; `desk resume` required to clear |
| M5-14 | Global limits | U/FI | Each limit in PLAN §13.2 halts entries at its threshold; cumulative-drawdown halt requires manual reset |
| M5-15 | P&L correctness | U | Hand-computed examples (gains, losses, fees, rent refund, failed-tx cost) match ledger to the lamport |
| M5-16 | Realized vs unrealized vs estimated | U | Labels correct; unrealized marked with source (`EXECUTABLE` / `ESTIMATED`); hypothetical proceeds never realized |
| M5-17 | Mode separation | U | PAPER writes only to `ledger-paper.db`; SHADOW writes no positions |
| M5-18 | Sleep/resume handling | FI | Simulated wall-clock jump of 30 min ⇒ entries halted, data marked stale, positions re-quoted and reconciled before resuming |
| M5-19 | **End-to-end PAPER lifecycle** | E2E | Recorded-fixture scenario: discovery → funnel → decision → sized entry → marks → exit by trailing stop → reconciliation → report; every step present in the audit trail |
| M5-20 | 72-hour live PAPER run | L/R | Report trades, rejections, costs by item, halts, quote-budget usage, RSS; zero unreconciled positions |
| M5-21 | Learning-book sizing (operator 2026-10-19) | U | Ticket = 10% × ($100 base + realized PAPER profit); losses never shrink it; 15% cap, fee floor, liquidity cap and cash still bind |
| M5-22 | $100-account replica | U | The same entries replayed under the real account's limits (2 positions, daily loss, trades/day, cumulative drawdown, fee floor) are reported separately |
| M5-23 | Fee realism (operator 2026-10-19) | U/L | Jupiter's platform fee is never deducted twice (recorded live comparison); network fees use ≥ 6 measured taker orders in 24 h at p90 × 1.5, else the conservative model; a round trip pays three transactions (buy, sell, close the token account); every closed trade records its fee breakdown and source |
| M5-24 | Conviction sizing (operator 2026-10-19) | U | Only H8 sizes up (1.5×, never above the 15% cap) and only with PASS net-expectancy evidence ≤ 7 days old for its exact version plus the extra signal; every entry records the policy, checks and the size it would have used |

Operator decisions of 2026-10-19 (PLAN §13.1.1) change how M5-02 and M5-14 apply: the absolute SOL cap was replaced
by the 15% percentage cap; the §13.2 account limits (2 positions, daily loss, drawdown, trades/day) bind the $100
replica and LIVE, while the PAPER learning book uses its own limits (quote-budget position cap, one position per
mint, order rate, consecutive-failure pause, stale-data and kill-switch halts, cumulative learning-loss halt).

---

## M6 — Statistical learning and confidence engine

| ID | Criterion | Kind | Pass condition |
|---|---|---|---|
| M6-01 | Bootstrap CI coverage | P/R | On 500 synthetic AR(1) (φ = 0.2) Student-t (ν = 3) series of length 300 with known mean, the nominal 95% stationary-block-bootstrap interval covers the true mean in 90–98% of runs (Monte-Carlo SE ≈ 1 pp); actual coverage reported. If coverage < 90%, the method is changed (e.g. wider blocks, studentized bootstrap) — the threshold is not lowered |
| M6-02 | Walk-forward splitting with purge/embargo | U | No training sample's label window overlaps a test sample; embargo ≥ max holding period; final untouched segment used at most once per experiment (registry enforced) |
| M6-03 | Leakage detector | U | A deliberately leaky feature (uses future price) is caught by the point-in-time audit |
| M6-04 | Calibration metrics | U | Brier, ECE, reliability bins match reference implementations on fixed data |
| M6-05 | Counterfactual tracking | U/L | Rejected candidates receive +5m/15m/1h/6h/24h outcomes; 10% sample has executable quote checks; survivorship test: dead tokens remain in the dataset |
| M6-06 | **No-edge data is never promoted** | P | On 1,000 synthetic zero-mean (after cost) datasets with realistic tails: the expectancy dimension alone passes in ≤ 7% of runs (nominal 5%; allowance for Monte-Carlo error ≈ 0.7 pp SE), and the full matrix reaches `ELIGIBLE` in 0 runs |
| M6-07 | Real edge with enough data passes statistical dimensions | P | Synthetic +4% mean, σ = 25%, n = 600 ⇒ expectancy and reliability dimensions PASS in ≥ 80% of runs |
| M6-08 | Deterioration detection | U/P | Series whose mean drops from +4% to −2% halfway triggers the alarm within the documented delay |
| M6-09 | Confidence matrix cells | U | Every cell has estimate, CI, evidence count, status; headline readiness derived only from rules (no free parameters from models) |
| M6-10 | Experiment registry is append-only | U | Updates/deletes rejected; every parameter search and comparison recorded |
| M6-11 | Champion/challenger | U | Challenger cannot replace champion without criteria met **and** a recorded operator approval; history preserved |
| M6-12 | Multiple-testing correction | U | Holm adjustment applied across concurrent challengers; reported alongside raw p-values |
| M6-13 | Local model predictions logged and benchmarked | U | Every local statistical model score is stored with model version, training window, feature-set version and inputs hash; evaluation reports compare it against B0/B1/B2 out-of-sample |
| M6-14 | Local models have zero influence until validated | U | Decision hash identical whether the model scores 0.0, 1.0 or is unavailable while `model_influence = none`; `portfolio`, `risk`, `security`, `execution` and `desk_signer` cannot import model-scoring modules (import-graph test) |
| M6-15 | Evidence council has zero influence | U/I | No module under paper, live, signer, risk, strategies, discovery, security or features imports the council, and the council imports no trading module (import-graph test); it writes only its own research.db records (ledgers and market evidence byte-identical before and after); a council failure is contained in its job |
| M6-16 | Council records are causal and reproducible | U/I | Every input ended and was received by the decision time (contract check); a 15-minute bar exists only from its complete native bars; SOL minutes count only once closed beyond the provider's 60 s cache; re-advising a decision after later data arrived gives the same input hash and stance; records are append-only and never rewritten |
| M6-17 | Council abstains honestly | U/I | Young (< 96 h first seen), illiquid, security-blocked, unidentified or out-of-cohort tokens, short or stale history and unmeasured costs are ABSTAIN with their reason; a measured cost failure is OPPOSE; a detected C2 dislocation without its pre-event panel is ABSTAIN; at most 20 cohort members per UTC day |
| M6-18 | Council adds no request and states its limits | U/I | Benchmark minutes are stored from the existing market-context payload and rebuilt from retained raw payloads with their original receipt times; the report says BLOCKED with no closed primary trades and INSUFFICIENT EVIDENCE below 300 |

---

## M7 — Dashboard and operations

| ID | Criterion | Kind | Pass condition |
|---|---|---|---|
| M7-01 | Performance | R | Initial load < 2 s on the operator's laptop (localhost, built bundle); live updates via SSE without visible jank at 1 update/s |
| M7-02 | Panels | M | Summary bar, scanner, active position (with clean empty state), strategy/confidence, execution/audit, system health, charts listed in the spec all present with real data |
| M7-03 | Plain-English status copy | M | Statuses use the documented phrases; every metric has a tooltip explaining what it measures and whether it is in-/out-of-sample |
| M7-04 | XSS resistance | U/E2E | Fixture tokens named `<img src=x onerror=alert(1)>`, `"><script>…`, and RTL/zero-width Unicode render as inert text; CSP header present; no remote images requested |
| M7-05 | Localhost binding & CSRF | U | Server binds 127.0.0.1 by default; control endpoints reject requests without session token/CSRF header |
| M7-06 | LIVE cannot be armed from the UI | U | No API route arms LIVE; the Live page shows gate statuses and offers Disarm only |
| M7-07 | Controls | E2E | Start shadow, start paper, stop entries, run report, export trade history (CSV + JSON), inspect candidate, review strategy, view confidence detail — all work |
| M7-08 | Design system | M | Tokens defined in one place; contrast ratio ≥ 4.5:1 for body text and ≥ 3:1 for large text/UI glyphs (automated check); LIVE frame styling visible on every page |
| M7-09 | Telegram | U/L | Allow-listed chat `/stop` creates STOP; other chats ignored; daily report labelled with mode; no secrets in messages |
| M7-10 | systemd user services | M/R | `desk up` installs/starts services; `desk down`/`desk status` work; services restart after a crash; memory caps applied |
| M7-11 | Backups and recovery | FI/R | Hourly ledger backup + daily market/research backups rotate; restore procedure from RUNBOOK reproduces an identical ledger (checksum) |
| M7-12 | **Beginner operability** | M | Operator completes the scripted checklist (start paper, find why a token was rejected, read confidence status, stop entries, export trades, restore a backup) without editing code |
| M7-13 | Sleep inhibitor on the real laptop | M/R | With an open paper position, closing the lid / idle timeout does not suspend (or, if Cinnamon ignores the inhibitor, this is documented and the resume path of M5-18 is shown working) |
| M7-14 | Paper trading tab (operator 2026-10-19) | U/E2E | Every recorded look, skip, buy and sell appears in plain English with its reason; open positions show hard stop, trailing stop and time limit; closed trades show fees and observed/stress results; reads are bounded (latest 100 trade/skip events + 50 decisions, 200 trades) and decision contexts never reach the browser; no horizontal scroll at 390 px |

---

## M8 — LIVE execution infrastructure (no real-money transactions)

| ID | Criterion | Kind | Pass condition |
|---|---|---|---|
| M8-01 | Key isolation | U | Only `desk_signer` can import the keystore module (import-graph test); keystore file must be 0600 or the signer refuses to start |
| M8-02 | Encrypted keystore | U | Wrong passphrase fails; ciphertext contains no key material; key never logged (log capture test) |
| M8-03 | Intent-only interface | U | Signer rejects raw transactions, unknown fields, expired intents, amounts above caps |
| M8-04 | Transaction inspection | F/U | Crafted transactions with a non-allow-listed program, a SOL transfer to a foreign address, a wrong fee payer, or output to a foreign account are all refused |
| M8-05 | Simulation required | U | No simulation result ⇒ no signature; balance deltas outside tolerance ⇒ refusal |
| M8-06 | Gate matrix | U | For each of the gates in PLAN §16.5, LIVE refuses when that single gate is missing (exhaustive table test) |
| M8-07 | No blind resubmit | FI | Simulated `/execute` timeout after submission ⇒ status polling until `lastValidBlockHeight`; no second submission while the first may still land |
| M8-08 | Idempotency & crash recovery | FI | Signer killed mid-intent ⇒ restart reads journal; same intent never signed twice |
| M8-09 | Zero-cost path validation | L | `/order` with read-only taker + `simulateTransaction` (sigVerify off) succeeds for a liquid token; nothing is sent |
| M8-10 | Emergency halt | E2E | STOP + `desk liquidate --all` (against mocks) attempts every exit and reports per-position success/failure truthfully |
| M8-11 | No real money moved | M | Hot wallet balance unchanged (or zero) at milestone end; no transaction signatures from the hot wallet on-chain |
| M8-12 | Restricted withdrawal | U | Only to `live.withdraw_address` with the exact typed confirmation; unregistered/empty address, mismatch, or amount above balance − reserve ⇒ refused; the signed transaction contains a single System transfer to that address |
| M8-13 | LIVE entries | U/I | Only typed-approved hypotheses decide (baselines never trade LIVE); the §13.2 account limits block before any request (2 positions, trades/day, order rate, daily loss, daily drawdown, and cumulative drawdown and consecutive failed transactions, which persist until a typed `desk live resume`); the ticket is 10% of min(equity, allocation) within every cap; a fresh exact-size quote and its sell-back pass the strategy's round-trip cost limit; the same per-intent authorization the signer applies is checked first and a refused intent is never sent |
| M8-14 | Booked from the chain | U/I | Positions, sales, rent refunds and failed-transaction fees are booked from the wallet's measured balance changes (without them: the intent's guaranteed bounds, flagged); the LIVE ledger's cash moves exactly as the wallet did and reconciles |
| M8-15 | Lost answers | FI | Every signer request is journaled before it is sent; a lost answer is resolved from the signer's journal before that position or any new entry proceeds and is never resent; a request that never arrived is closed only after its expiry; a restarted signer measures what landed against the balances journaled at signing |
| M8-16 | LIVE exits | I | The LIVE guardian sells the wallet's on-chain token balance against a fresh quote, sells any remainder, closes the token account and books the position at what the wallet received; refused, failed or expired exits are retried, blocked after three and stay supervised; tokens missing from the wallet halt LIVE entries for reconciliation |

---

## M9 — Forward validation and readiness assessment

| ID | Criterion | Kind | Pass condition |
|---|---|---|---|
| M9-01 | Forward PAPER operation | R | ≥ 60 calendar days of PAPER data on newly observed markets (gaps reported) |
| M9-02 | Report generated from data, not prose | U/R | `READINESS_REPORT.md` is produced by `desk report readiness`; every number traces to a query; regenerating gives identical output |
| M9-03 | Verdict | R | Report states exactly one of: INSUFFICIENT EVIDENCE · FAILED VALIDATION · CONTINUE PAPER TESTING · ELIGIBLE FOR MANUAL LIVE REVIEW, derived from PLAN §14.3 rules |
| M9-04 | Disclosure | M | Report lists unresolved risks, data gaps, profile differences (observed/conservative/stress), and states that past paper results do not guarantee future returns |
| M9-05 | No self-promotion | U | Any verdict leaves the mode unchanged; LIVE still requires the separate gates |

Tooling for M9-02…M9-05 is implemented (2026-10-19): `desk report readiness` writes
`DESK_HOME/reports/READINESS_REPORT.md`; while PAPER runs, the collector refreshes the confidence matrix and the
report every 6 hours, and the dashboard shows the latest verdict. M9-01 and the verdict itself need ≥ 60 calendar days
of forward PAPER evidence on the laptop; no tooling can shorten that.

## R — The Robinhood desk (docs/ROBINHOOD.md)

| ID | Criterion | Kind | Pass condition |
|---|---|---|---|
| R-01 | Signed API client | U | Reproduces the docs' published Ed25519 signature; signs the exact path, query and body sent; read-only methods only |
| R-02 | Local key, read-only credential | U/M | `desk rh keygen` writes an owner-only key that is never overwritten; setup asks for read actions only |
| R-03 | History without look-ahead | U | Only closed Coinbase bars are stored; a session's features never include its own day's bar |
| R-04 | Frozen strategies and baselines | U | Spec hashes recorded; R1/R2/R3 entries and exits, hold-BTC and random baselines behave as written |
| R-05 | Realistic PAPER fills | I | Both routes estimated for the exact quantity; cheaper buy, better sell, worse of two estimates booked; round trip above 2% refused |
| R-06 | Ledger integrity and halts | I | Entries and closed positions immutable; hard stops on the best bid (none on hold-BTC); $1,000 cumulative loss halts entries |
| R-07 | Works before the key exists | I | History and signals recorded as SKIP NO_ROBINHOOD_PRICE; nothing trades |
| R-08 | Separate dashboard | U/B | Port 8766, loopback only, read-only; $100-account view, coins, coin "why" checklist, strategies vs baselines, light/dark, phone width |
| R-10 | Continuous v2 checks | I/B | Closed 15m / 1h / 4h / 1d readiness; afternoon checks without a daily session; no synthetic gap filling, stale inputs or restart replay; chart intervals and version-separated evidence |
| R-09 | Forward evidence | R | Weeks of PAPER on Robinhood's prices with measured costs; until then the history simulation is labelled as such |

R-01…R-08 pass automated tests (2026-10-20). R-09 needs the operator's read-only key and time.
