# Durable research handoff

Date: 2026-10-19. User explicitly requested research/specifications while Claude finishes the app; disregard
the earlier usage-threshold instruction. No percentage usage meter was available and none was claimed.

## Completed

- Audited repository commit32344daf517c58f88f87e6a1eb21ba86f4fcb035, including required project documents,
  configurations, H1–H7/B0–B2/R1, feature/data lineage, costs, statistical helpers and their tests.
- Acquired and verified primary document copies; detailed access/read scopes, exact claims/locators and
  bibliographic metadata live in the evidence reviews/manifests. Do not repeat source searching before reading them.
- Chose exactly C1_BRC and C2_ECDR; wrote equations, distinct profiles, default rules, missingness, risk/cost
  qualification, numerical illustrations, falsification and simpler baselines. All defaults remain unvalidated.
- Wrote strict research-only advisory contract, no-voting council policy, validation/ablation plan and future
  implementation sequence with free-tier/laptop budgets. No runtime files or production policy changed.
- Corrected quote-budget arithmetic: two pools at5m would require48baseline calls/hour. v1uses15m baselines
  (16/hour), at most14event/final calls/hour, hard30/hour and2/minute; lack of quota means ABSTAIN.
- Recorded H7/H1 baseline blockers and real data gaps. Do not remove provenance/security requirements to
  manufacture a trading sample. Coordinate separately with Claude's final version before any implementation.
- Verified numerical illustrations and profile indexing with verify_design.py; validated the research JSON
  Schema using the already-available jsonschema library, including rejection of LIVE/influence/third-advisor
  and fabricated probability fields. Output is verification.json. This is not a backtest. Ruff passed.

## Reproduction

Run `python research/council/verify_design.py` from this checkout for specification consistency and example
arithmetic. This command has no provider, credentials, ledger, collector or wallet dependencies. The returned
counts measure checks, not strategy performance. Source PDFs were temporarily acquired under
`/tmp/council-sources`; durable manifests contain URLs, hashes and reading scope, not redistributed papers.

## Next actions, in order

1. Read README.md in this directory, STRATEGIES.md, COUNCIL.md and both plans. Resolve any cross-document
   disagreement before writing application code. Research files are not installed configuration.
2. Wait for Claude's app handoff; inspect its actual new commit and AGENTS instructions. Re-audit H7 feature
   provenance, migration chronology, source interval semantics and actual primary trade counts. Do not patch
   the running collector, reuse synthetic OK features, or silently alter baseline versions.
3. Register the two frozen experimental specifications and data/availability contract; produce a coverage-only,
   zero-additional-network-call SHADOW sidecar. Persist independent research records; primary output hashes
   must remain identical with the council enabled, disabled or crashing.
4. Normalize synchronized benchmark history from existing raw references only if original receipt times and
   interval semantics survive. Mark incomplete reconstruction unavailable; start prospective capture as needed.
5. Build ECDR panels only with pre-event enrollment and verified applicable reserve/L2 state. First version
   abstains where current measurements are absent. New probes require shared exit-prioritized budget tests.
6. Establish qualified equity/centralized-crypto data rights, calendars/corporate actions, prices, depth, fees,
   lot sizes and access before calling those profiles executable. Do not assume a public archive licenses
   production proprietary use or that a $100 account can trade an integer share.
7. Complete causal replay tests and matched offline ablations, then genuine frozen forward evidence under the
   validation plan. Only separately qualified future PAPER filtering may gain influence. Ranking/sizing/LIVE
   remain separate, gated projects; no automatic promotion or larger tickets from paper profits.

## Unknowns and restrictions that must remain visible

- No demonstrated edge for either advisor on any KESTREL profile; no new backtest/Monte Carlo/profit estimate.
- Primary all-NO-TRADE behavior can make filtering efficacy unidentifiable; do not hide this.
- Paper lookback/horizon transfer is a hypothesis, not established empirical scale invariance.
- Reversal may represent permanent information, a rug or adverse selection rather than temporary dislocation.
- Residual beta can be unstable and omits other factors; USD strength is not SOL-denominated net gain.
- Stop/target/time limits do not guarantee a realizable exit; unresolved exposure remains counted.
- Dependence, shared inputs, multiple attempts, missing exits and unavailable data can make evidence insufficient
  far beyond minimum trade/calendar floors. The correct report may remain NO DEFENSIBLE INCREMENTAL EDGE.

## Implemented minimum version (Claude, 2026-10-20, branch `claude/funny-rubin-gq99vj`)

The "build first" sequence of implementation_plan.md is in the application. Still SHADOW, influence NONE, zero added
requests. Re-audit before the next research step:

- **Re-audit of the blockers.** H7's universal veto was fixed in the primary engine as evaluator `shadow-rules-v4`
  (an overlay may act on an UNVERIFIED value; MISSING still vetoes), recorded and replayable as its own version;
  H1's migration-time and H2/H5/H6 provenance gaps remain and still produce NO_TRADE. **Bar semantics are resolved:**
  GeckoTerminal OHLCV time is the interval START, documented by CoinGecko and measured
  (`probe_ohlcv_semantics.py`, `research/evidence/2026-10-20/council_ohlcv_interval_semantics.json`: the newest bar
  kept changing after its own time; each fully observed trade minute matched the start-labelled bar to 1e-11 USD;
  six 5-minute bars equal the sums of the 1-minute bars labelled t..t+240). Actual primary trade counts can only be
  read on the laptop (`desk paper-report`).
- **Contracts and specs.** `src/desk/contracts/advisory.py` (this schema plus the semantic rules of COUNCIL.md) and
  `src/desk/council/specs.py` (exactly C1_BRC v1 and C2_ECDR v1 with the solana_meme_15m values, hashed; a test
  checks them against specifications.json). Schema change: `code_commit` became `code_sha256`, a hash of the council
  code and contract, because the laptop may run without git metadata; verify_design.py was updated to match.
- **Data.** Market migration 0009 `benchmark_bars`: SOL/USD minutes stored from the payload market context already
  fetches, first observation immutable, and only once the minute had been closed longer than the provider's 60 s
  cache when received (a younger "closed" minute may be a cached partial bar). `desk council rebuild-benchmark` (also
  automatic once) rebuilds them from retained raw payloads with the original receipt times; payloads purged by the
  14-day raw retention stay missing. Token bars are the existing immutable 5-minute `ohlcv_bars`; their ingestion
  accepts a bar once closed at receipt, so a bar first seen within 60 s of its close may be a cached partial bar.
  That residual risk is disclosed, not corrected. Raw benchmark packets are not pinned; the normalized immutable rows
  are the retained evidence.
- **Calculations.** `panel.py` (15-minute bars only from complete native bars ended and received by the decision; the
  longest consecutive run common to token and SOL), `brc.py` (Decimal OLS, MAD scale, held-out Z, half-sample beta
  check; matches an independent numpy fit), `ecdr.py` (event test). C2 has no watcher, so a detected dislocation is
  ABSTAIN `PRE_EVENT_BASELINE_MISSING`; complete evidence without one is CAUTION `NO_REVERSAL_THESIS`.
- **Economics.** C1 SUPPORT needs the decision's exact-ticket pair from its security assessment: both legs received
  within 10 s before the decision and 5 s of each other, the sell leg selling exactly the bought amount, cost
  `(Q - Q_back + modelled network fees for the round trip)/Q <= min(3%, discovery limit)`. Otherwise ABSTAIN
  (`ECONOMICS_*`), or OPPOSE `ROUNDTRIP_COST_ABOVE_CEILING` when measured and too high.
- **Universe.** Security PASS within its age, first observed >= 96 h, verified liquidity >= $100,000, known pool
  identity, then a first-come 20-member cohort per UTC day (`council_members`, append-only). Admission follows the
  merged decision order of all sources.
- **Records and runner.** Research migration 0004: `council_specs`, `council_members`, `council_advisories`
  (append-only, unique per advisor/spec/source/decision; a rewrite with other inputs is refused) and a cursor. The
  runner (`src/desk/ops/council.py`) reads SHADOW, PAPER and LIVE decisions in recorded order, at most 300 per pass,
  in a worker thread every 15 minutes inside `desk paper` (`[research] council = "record"`, the default; "off" stops
  it) and via `desk council run`. Input references are compact row ranges; `input_sha256` covers every bar's digest.
- **Report and view.** `desk council report` (coverage, stances, top reasons, disagreements, the opportunity-matched
  four-arm diagnostic with OPPOSE removing an entry and its capital kept as cash; verdict BLOCKED, NO FILTER ACTIVITY
  or INSUFFICIENT EVIDENCE below 300 closed trades). The dashboard Research view shows the latest records read-only.
- **Tests.** `tests/unit/test_council.py`, `tests/integration/test_council_runner.py`: contract/schema parity,
  frozen values, independent-fit agreement, window separation, abstention cases, causal panels, cache rule,
  raw rebuild, cohort limit, reproducibility after later data, import graph both ways, nothing written outside the
  council's records, failure containment, report and dashboard. About 65 ms per decision with full history in
  the container (not a laptop measurement).

Still open, in order: the C2 two-pool watcher with its shared, exit-prioritized budget (implementation_plan §6);
decoded reserve history for the depth test; laptop RSS/disk measurements; genuine forward records over the required
calendar time; the confirmatory paired test of validation_plan.md (calendar-block bootstrap, Holm) once a
nondegenerate primary tape exists. Equity and centralized-crypto profiles remain unimplemented (no data rights).
