# Future implementation plan: two SHADOW advisors

This is a research handoff and future implementation plan, not an integration or deployment. Claude is completing the application separately. No collector, provider, configuration, ledger, strategy, signer or UI behavior is changed by this document. Reinspect the final application branch and its migration sequence before implementation.

The two immutable advisor identities are **C1_BRC**, benchmark-residual continuation, and **C2_ECDR**, temporary execution-dislocation recovery reversal. Both begin with **zero influence** on entries, rankings, exits and sizing. Data failure produces ABSTAIN with reasons; it does not reduce the existing security requirements.

## 1. Freeze the integration baseline

1. Record the final application commit, configuration hash, current strategy/evaluator versions, migration checksums and research specification hashes. Do not edit applied migrations or ongoing acceptance-run inputs.
2. Review `repo_audit.md` and `design_review.md`. Confirm H7's provenance-related fail-closed consequence and H1's missing verified occurrence timestamp against the final code. Keep those as explicit operational findings; do not quietly change the primary strategy to make the council appear useful.
3. Define a versioned experiment registry entry for the two advisors, their simple baselines, data domains, parameters, exclusions, cost assumptions, counterfactual horizons and evaluation arms. Register discarded trials and later revisions as well as favorable ones.
4. Record the primary account experiment separately from the $100,000 PAPER learning cash book and its $100-account replica. Advisors never reinterpret learning-book cash as real position-size capacity.
5. Freeze the four comparison arms: unchanged primary, primary with C1 evidence, primary with C2 evidence, and primary with both. During the initial record-only phase these are analytical counterfactual policies; all real application decisions remain identical. A primary arm that produces NO_TRADE is reported honestly.

## 2. Add strict research contracts

Proposed new files, subject to the final repository layout:

| File | Responsibility |
|---|---|
| `src/desk/contracts/advisory.py` | Frozen advisory/specification/input-reference contracts; strict enums, finite values, explicit units and timestamp ordering |
| `src/desk/council/specs.py` | Exactly two advisor identities and immutable versioned parameter manifests; no automatic tuning |
| `src/desk/council/panel.py` | Causal bounded panel queries, frozen membership, interval aggregation, availability/gap/identity checks |
| `src/desk/council/brc.py` | Pure C1 regression and held-out residual calculation |
| `src/desk/council/ecdr.py` | Pure C2 event-state evaluation from already observed evidence |
| `src/desk/council/evaluate.py` | Deterministic advisory production; no trading imports or provider requests |
| `src/desk/storage/council_repo.py` | Append-only research records and idempotent writes |
| `src/desk/ops/council.py` | Opt-in bounded SHADOW runner, reports and replay; no money-ledger writes |
| `src/desk/stats/council.py` | Separate batch paired-ablation, dependence and uncertainty reports |

An advisory records advisor/spec/code version, original primary decision hash, asset and pool/venue, observation interval, first reception and decision times, direction/horizon, SUPPORT/CAUTION/OPPOSE/ABSTAIN, measurable rationale, freshness/missingness, evidence counts and uncertainty method, applicable domain/regime, shared dependencies, normalized input hash and exact source row/raw references. No score is labeled a probability without an independent calibration experiment.

Hash canonical numeric strings, sorted input references, source identity, bar/reception times, units, cohort membership and frozen parameters. Do not hash mutable display metadata into historical evidence. Preserve exact token/lamport integers as strings where required; an integer-like price or amount must not pass through JavaScript floats.

Acceptance: an identical evidence set reproduces the same advisory hash; future rows, changed display metadata and an unavailable council cannot change the primary decision hash. No council module imports or writes signer, guardian, risk authorization or money-ledger state.

## 3. Persist the evidence needed for honest calculations

Append new migrations **after the highest migration in the final application branch**. Allocate actual version numbers at implementation; do not assume that the next market migration is still 0008. Existing migration checksums remain unchanged.

Research additions should hold immutable council specs, experiment manifests, frozen cohort membership, run metadata, advisory observations and separately versioned evaluation reports. Use append-only protections and unique identities to reject conflicting replay writes. Store compact source references rather than copying large raw packets into every advisory.

Market additions should normalize benchmark intervals and, when implemented, verified reserve/quote-event evidence. Every record needs instrument/pool/venue/source identity, event start/end, first received time, units, version and raw digest. Capture any later revision as a new revision record; it must not revise the information available to an earlier decision.

1. Reuse the SOL minute OHLCV payload already requested by the collector. Normalize its actual minute bars and reception times; aggregate a 15-minute bar only from all fifteen aligned closed one-minute bars.
2. Aggregate a token 15-minute bar only from all three aligned closed five-minute bars of the same pool, mint, provider, quote basis and accepted revision policy. Do not forward-fill, interpolate, splice pools or manufacture no-trade candles.
3. Verify provider timestamp semantics before relying on either series. The current token OHLCV contract still describes start/end semantics as unverified; that blocks verified council signals. Keep ABSTAIN until the contract is supported by documented and observed interval evidence.
4. Check both event completeness and historical availability: a bar backfilled today cannot become an input to yesterday's advisory. Future raw reconstruction must retain the original packet reception time, not assign the reconstructed file's write time.
5. Freeze admission and removal rules for a bounded cohort. Retain failed, drained and no-longer-indexed identities and explicit missing outcomes; do not retrospectively keep only surviving pools or current chart targets.
6. Decode verified constant-product reserve packets to normalized base/quote quantities when required. Current `PoolVerification` stores derived USD liquidity and raw references, not the original normalized X/Y fields. Persist or reconstruct these quantities causally; never substitute USD TVL for invariant depth.

Acceptance: a deliberately incomplete, unaligned, revised, future-received, wrong-pool, wrong-mint or unsupported interval remains unusable with a specific reason. Normalization adds no provider requests in the minimum version.

## 4. Build C1_BRC as a pure calculation

The older-memecoin research configuration uses 15-minute returns: 288 training returns, 16 held-out signal returns and one skipped latest return. This means 305 consecutive returns and 306 closed price observations, spanning **76 hours 15 minutes** between first and last close. Minimum first-seen age is **96 hours**; that separate threshold does not replace the required same-pool continuous history.

1. Align token/USD and SOL/USD return intervals exactly. Require the specified reception-time availability and current freshness. Persist the fixed cohort and benchmark identity.
2. Fit OLS alpha and beta on training observations strictly before the signal window. Record training boundaries, coefficients, benchmark variance/design conditioning and robust residual scale. Zero/insufficient benchmark variation, nonfinite coefficients or zero MAD causes ABSTAIN.
3. Compute held-out residuals using the frozen coefficients. Scale their sum by the training residual MAD scale and square root of signal count. The resulting Z is a diagnostic, not a normal test statistic, p-value, probability or estimate of executable profit.
4. Apply the preregistered long-only support threshold and abstention rules. Check recent risk evidence even though the newest return is skipped from the signal; skipping does not ignore an ongoing crash or stale route.
5. Record the eight-hour counterfactual horizon without changing the current guardian's exits. Evaluate raw momentum and beta-one relative momentum as simpler baselines before attributing value to estimated beta.
6. Report token/USD, SOL/USD and net token/SOL performance separately. Existing account cash is SOL-denominated; a positive residual or USD gain alone is not account outperformance.

Acceptance: hand-calculated fixtures reproduce indexing, alpha/beta and residual scale; the signal window never enters fitting; extreme but valid losses remain in evidence; gap, singularity and current risk failures abstain; no provider call or production account effect occurs.

## 5. Specify C2_ECDR before enabling any extra observation

C2 is experimental. Two pre-enrolled eligible constant-product pools are the maximum prospective watch cohort. It requires a genuine pre-event same-ticket cost/depth baseline; the current sparse quote records usually cannot establish that baseline, so the minimum version reports ABSTAIN.

1. Define the negative 15-minute token/SOL innovation against the preceding 96 completed returns, excluding the event. Use median centering and scaled MAD; require finite positive scale and complete coverage. Apply the independently specified SOL-move boundary and domain rules. Fixed beta-one relative return does not prove an idiosyncratic shock.
2. Freeze a pre-event constant-product reserve baseline with raw evidence, units, slots, age and quality. Require current `sqrt(X*Y)` depth at least 95% of that baseline for the same pool and mint units. Reject concentrated-liquidity, virtual-reserve and unsupported extension/fee structures for this specification.
3. Freeze a strictly pre-event same-ticket quote-cost baseline: **six complete quote pairs at a 15-minute cadence within the 90 minutes before the event bar starts**. The latest eligible baseline pair must have arrived before that start and no more than 15 minutes earlier. Exclude all observations received during or after the event bar from its baseline. Q is the same SOL input amount for each observation; each reverse leg sells the exact token quantity returned by that observation's buy quote. Do not keep the token reverse amount fixed while token prices change. Keep route, fee/slippage and economic basis comparable; otherwise ABSTAIN from the proposed causal interpretation.
4. Record the cost spike and two qualifying recovery quote pairs, each internally fresh when observed, with consecutive spike/recovery observations separated by at least five minutes. At final decision, both legs of the final exact-ticket pair must meet the existing ten-second age limit. The second recovery pair can serve if still fresh; otherwise a new pair is required within the fixed event quota. Missing routes are explicit unavailable observations, not infinite measured costs. Do not start a required pair when its quota is unavailable; record ABSTAIN instead.
5. Anchor the event to the shock bar's end and expire the proposal after 30 minutes, irrespective of late reception. Check current depth and risk evidence again at the decision boundary.
6. Require the observed bounce chronology and remaining gap specified in the frozen research contract. Convert half a log gap to `exp(gap/2)-1` before comparing it with arithmetic round-trip costs and margins. A two-percentage-point stress margin is an assumption, not an estimated edge. Do not double-count fees already included in quote output.
7. The 90-minute maximum hold and eight-percent stop are counterfactual exit policies. A vanished route remains impaired/blocked; do not assign a fictional eight-percent filled loss. Resolve intrabar target/stop ambiguity conservatively or from actual quote chronology.
8. Retain H2/H4/H7 overlap and disagreement labels. Neither C2 nor its experimental counterfactual can override primary security, STOP, guardian supervision, risk limits or unavailable LIVE authorization.
9. Admit at most two C2 events per instrument per day and at most two new event attempts across the panel in a rolling hour. Preregister the day boundary and event deduplication key. A 15-minute baseline deliberately loses temporal resolution: it can miss short cost spikes and confuse transient baseline variation with an event. Report that limitation and test sensitivity using genuinely denser observations when available; if the coarse baseline cannot reliably distinguish recovery, retain ABSTAIN rather than quietly increasing request frequency.

Acceptance: missing pre-event baseline or missing recovery/depth observations means ABSTAIN; a fully observed adverse sequence maps to OPPOSE under STRATEGIES.md rather than pretending its inputs are missing. No event with complete neutral evidence is a reversal thesis. Future minima and post-event baseline observations cannot create an entry; changing ticket, pool, mint, decimals, route basis or supported fee structure invalidates the sequence. Recompute target headroom from the fresh same-pool reference/L2 midpoint at the decision; an old candle close cannot stand in for a new opening price.

## 6. Keep API and memory budgets explicit

The minimum record-only version adds **zero network requests** and uses already available evidence. A later separately enabled observer must reserve exit capacity first through the existing shared provider limiter; it must not create a second uncoordinated provider client or budget.

| Budget | Proposed future constraint and interpretation |
|---|---|
| C2 v1 baseline | Two pools × two quote legs / 15 minutes = **16 Jupiter requests/hour** nominally. Require six complete pairs within the 90-minute pre-event window; latest pair must precede event-bar start by at most 15 minutes |
| C2 v1 event observations | At most two new event attempts/panel/rolling hour. Two complete three-pair sequences (spike and two recoveries) use **12 requests/hour** nominally; allow at most two additional final-recheck requests, for an absolute **14/hour** event allowance |
| Hard research ceiling | At most **two Jupiter requests per rolling minute** and **30 per rolling hour**, including retries and baseline/event/final observations, **also bounded by actual residual shared capacity**. The event subset independently cannot exceed 14 per rolling hour |
| C2 v1 event frequency | At most two events/instrument/day and two new attempts/panel/rolling hour; deduplicate attempts and record the fixed day boundary in the manifest |
| Quote timing | Spike and consecutive recovery observations must be separated by at least five minutes; both final quote legs must be within ten seconds at decision. Timing and quality requirements never expand the quota |
| Reserve/RPC evidence | Reuse preserved verified raw evidence where freshness allows; schedule any new reads explicitly. Track the provider's weighted credit charges; never assume one request equals one credit |
| Rolling cohort | At most 20 token pools and one benchmark; bounded queries and rolling numeric windows with explicit maximums |
| Incremental RSS | Target at most 100 MiB above baseline; measure on the Dell, including Python/data-array overhead |
| Point calculation | Target at most 100 ms for a 20-asset 15-minute calculation cycle, excluding database I/O; local target, not a measured result |
| Bootstrap | Separate manual/scheduled batch; reuse existing approximately 16 MiB index chunks. That index-chunk bound is not the total process-memory bound; record peak RSS and bound other arrays |

The v1 schedule is explicit: 16 nominal baseline requests plus at most 14 event/final requests fit the 30-per-rolling-hour research ceiling. Reserve quota for a complete linked pair before starting it; if that reservation or the shared exit-prioritized quota is unavailable, ABSTAIN. Sliding-window overlap, retries, errors and primary activity can make fewer than the nominal observations feasible. Never borrow guardian capacity or widen the quota to complete an event.

The coarser 15-minute baseline can miss brief spikes, makes the latest baseline older than a five-minute schedule would, and reduces causal resolution. Its six-pair quality gate and pre-event timing are mandatory. This limitation is a falsifiable part of v1: if observed baseline variability or missed events makes recovery unidentifiable, demote C2 to ABSTAIN/report-only. The zero-additional-call minimum version is unchanged; this future observation schedule is not started by the research plan.

Provider health, shared capacity and exit reservations can make an otherwise allowed research probe unavailable. Record the skipped opportunity and reason. Do not let an observational probe delay an exit or keep an old quote alive past its permitted age. Retry and backoff requests count toward the same limits.

## 7. Forecast storage, then measure actual growth

The following are planning scenarios, not laptop measurements:

| Scenario | Arithmetic | Forecast |
|---|---|---|
| Five-minute normalized observations | 21 series × 288 bars/day | **6,048 rows/day**; token bars already collected by the application are not all incremental council storage |
| Compact advisory records | Two advisors × 20 assets × 96 decisions/day × 2 KiB/record | **3,840 records/day**, 7.5 MiB/day, 450 MiB/60 days |
| Advisory rows plus illustrative index/overhead factor | 450 MiB × 2 | Approximately **0.9 GiB/60 days**, before raw packets and backups |
| Illustrative raw quote evidence | 2,880 packets/day × 4–25 KiB/packet | **11.25–70.3 MiB/day**; 30-day scenario approximately **338 MiB–2.06 GiB** before other APIs, database indexes/WAL and backups |

The 2,880-packet/day raw scenario corresponds to sustained two-per-minute collection; it is a deliberately conservative storage scenario, not permission to exceed the 30/hour research ceiling. The latter permits at most 720 research requests/day, before retained existing-provider evidence is counted. Retention duration is a scenario assumption; reconcile it with the application's current retention and evidence-pinning policies before deployment.

1. Measure actual row sizes, compressed packet distribution, daily new bytes, WAL high-water mark and hourly/daily backup duplication on the laptop using file metadata and compact reports.
2. Prefer normalized observations and source hashes/references over duplicated large contexts. Preserve enough pinned source evidence and specification metadata to reproduce each retained advisory; a hash without retrievable evidence is insufficient.
3. Keep immutable evidence and evaluated manifests, with explicit retention/tombstone policy for unevaluated bulk observations. Never delete the primary ledger, guardian evidence or a pinned evaluated sample merely to satisfy a council budget.
4. Reserve at least **30 GiB free disk**. When that threshold is breached, pause only new council records/probes and report the pause; do not stop the guardian, suppress ledger settlement or alter existing safety controls.

## 8. Validate semantics before evaluating an edge

Proposed new tests are meaningful property/behavior checks, not copies of implementation:

- `tests/unit/test_council_contracts.py`: units, timestamps, unavailable observations, canonical hashing and immutable spec revision.
- `tests/unit/test_council_panel.py`: full interval coverage, bar semantics, original reception time, cohort membership, same-pool/source boundaries, no implicit candles and no use of later revisions.
- `tests/unit/test_council_brc.py`: hand-calculated regression, explicit train/signal/skip boundaries, singular beta, zero MAD, outliers, skipped-bar current-risk checks and numeraire distinction.
- `tests/unit/test_council_ecdr.py`: pre-event baseline ordering, raw invariant units, route/ticket consistency, spike/recovery chronology, five-minute spacing, final ten-second freshness, expiry and missing exits.
- `tests/integration/test_council_replay.py`: real disposable SQLite migrations, duplicate idempotency, immutable evidence, replay identity and unchanged primary hashes/ledger with advisor success, failure and timeout.
- `tests/integration/test_council_budget.py`: research defers under exit demand; retries share global limits; no hidden HTTP fetch from report/UI reads; credit accounting uses declared weights.
- `tests/integration/test_council_ablation.py`: simultaneous account replay with matched capital/exposure, paired outcome alignment, zero-trade arms, impaired positions and calendar-block resampling across all arms.

Run relevant unit/integration checks, lint/type checks and a bounded laptop resource measurement. Synthetic checks establish calculation and isolation semantics. They do not demonstrate profitability, timed acceptance, calibration or LIVE readiness.

## 9. Collect prospective evidence and evaluate in batches

1. Record genuine forward advisories before their outcomes, with no policy influence. Keep historical reconstruction, synthetic fixtures and forward evidence in separate report fields/datasets.
2. Preserve full attempted-entry denominators, unavailable routes, failed/dead pool membership and ambiguous outcomes. NO_DATA is not automatically zero return or proven total loss; report identified bounds and missingness sensitivity.
3. Use chronological walk-forward research with a preregistered untouched final period. Purge overlapping outcome intervals and apply an embargo based on the maximum relevant horizon; C1's eight hours and C2's 90 minutes do not shorten a primary H6 horizon of 24 hours.
4. Evaluate paired net account outcomes for primary alone, plus C1, plus C2 and plus both under identical capital, exposure, costs, timing and common funding numeraire. Evaluate ranking, filtering and sizing effects in separate experiments; a filter result cannot justify larger positions.
5. Resample calendar blocks jointly across all arms/assets and stress longer blocks, shared failures and outages. Report net expectancy and uncertainty, drawdown/tail loss, turnover, capacity, cost sensitivity, selection overlap and disagreement performance. A fixed trade count or p-value alone is not promotion evidence.
6. Correct for the full registered family of tried hypotheses and policy variants. Report contradictory and regime-specific results; retain simpler baselines and the unchanged primary even if a complex advisor looks better on one slice.
7. Promotion remains a future separately authorized policy version after objective incremental-benefit, data-integrity and risk criteria are satisfied. Any deterioration, unsupported domain, stale data or loss of reproducibility demotes the advisor to ABSTAIN/report-only. LIVE entry authorization remains independently required and unavailable until its production work is complete.

## 10. Add an honest report view last

After records reproduce, add a bounded read-only council view to the existing report/API/dashboard. Show the two named advisors, their latest state, horizon, observation age, applicable domain, missing inputs, evidence count and experiment version. Display disagreements and shared dependencies; do not average their scores, create a majority-vote trigger or imply probabilities.

Use the existing shared SSE/update path and bounded queries. Cache completed batch reports. Never run regressions or bootstraps on every browser refresh, and never fetch provider data because a user opened a chart.

## Prioritized minimum version and blockers

**Build first:** immutable specs/contracts → causal data coverage and abstention reports → normalized benchmark persistence from existing payloads → bounded C1 calculations → C2 state/abstention evaluation from existing evidence → replay/isolation tests → prospective reports. Add any C2 watcher only in a later explicit implementation step with shared capacity and retention measured. UI follows verified records.

**Blocked by new observations or unresolved contracts:** provider bar start/end semantics; synchronized same-pool history; valid pre-event C2 cost/depth baselines; route comparability; real failure/exit cost distributions; regime breadth; sufficient independent prospective evidence; and the primary engine's separately recorded provenance limitations.

**Other markets:** no equity feed, corporate-action/delisting treatment, calendar implementation or licensing approval currently exists. Do not present stock experiments as implemented. Public crypto archives such as Binance's can support separately scoped historical research only after checking actual venue coverage, terms, access restrictions and the operator's jurisdiction; an accessible archive is not permission or capability to trade that venue. Neither equities nor exchange-traded crypto results establish an edge in newly issued Solana memecoins.
