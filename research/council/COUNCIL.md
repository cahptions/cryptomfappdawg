# Evidence council interface and disagreement policy

This is a proposed deterministic research interface, not an installed decision service.
Exactly two advisors exist: C1_BRC and C2_ECDR. The primary engine remains the only source of its original
decisions. Both advisors have `mode=SHADOW`, `influence=NONE` and no order, sizing, risk or signer authority.

## Meaning of the four stances

Every stance refers to **a prospective long candidate**, not agreement with whatever the primary happened
to do. Record the original primary action and immutable decision hash separately. SUPPORT while the primary
chooses NO_TRADE is a research disagreement, not permission to create an entry.

| Stance | Meaning | Effect on current engine |
|---|---|---|
| SUPPORT | Fully observed, eligible inputs fit the advisor's stated long hypothesis and current cost ceiling | None |
| OPPOSE | Fully observed evidence contradicts the long hypothesis or establishes the stated execution failure | None |
| CAUTION | Required inputs are known but the specified long thesis is neutral, absent or incomplete as a setup | None |
| ABSTAIN | Required evidence, eligibility, model stability or economic measurement is unavailable/conflicting/stale | None |

Missing evidence is not a negative outcome, and ABSTAIN is not a zero-return forecast. Preserve a candidate
mathematical signal even when a data/economic gate forces ABSTAIN. A diagnostic Z-score is not a probability,
expected return, significance test or confidence interval. A SUPPORT record does not certify net profitability.

## Record contract

[advisory.schema.json](advisory.schema.json) defines required identities, original decision time, calculation
time, latest event end, first-available times, expiry, fixed horizon, stance/direction, reason codes, raw input
references, evidence counts, missingness, model/regime status, shared dependencies and exact-ticket economics.
Forward evidence count may truthfully be zero; effective sample size and return intervals must be null when
unknown. This schema explicitly disallows an asserted calibrated probability or expected return.

JSON Schema cannot enforce all financial semantics. A future application validator must additionally enforce:

1. UTC-normalized event end and first-available time of **every** input are <=original decision time. Data
   fetched after an original primary decision may inform only a later decision. `computed_at >= decision_at`.
2. For SUPPORT/OPPOSE records, enforce the relevant missingness/model/eligibility/quote rules. An observed
   route failure is known adverse evidence; absent route measurements are unknown. Security failure never
   becomes a council override: emit ABSTAIN with the primary hard-gate reason.
3. Economic SUPPORT requires current exact-ticket economics; descriptive scores alone cannot satisfy it.
   An OPPOSE based only on a measured price signal can carry UNMEASURED economics, but cannot assert the
   profitability of an alternative or skipped trade. This distinction prevents a sell-route failure from
   being suppressed merely because no successful sell amount exists.
4. Timestamp, quote and reserve expiries are checked separately. Never refresh old economic evidence by
   assigning a new advisory timestamp. SUPPORT expiry is the earliest input/safety/quote boundary; if already
   expired at calculation, persist the historical record as expired rather than offering it as current advice.
5. Quantity fields are exact integer strings; prices/cost fractions are decimal strings with explicit currency.
   Different ticket amounts, decimals, pools, source lineages and corporate-action states cannot be mixed.
6. Reject NaN/infinity, zero-scale fits, unverified interval semantics, unsupported invariants and future input
   revisions. No imputation, inferred organic volume, market-cap guessing, or historical first-seen backdating.
7. Serialize the ordered input manifest with UTF-8 canonical JSON: sorted keys, compact separators, explicit
   nulls, no floating NaN, UTC-Z dates, integer raw amounts/decimal observations as strings. Sort source refs
   by `(store,row_id,raw_sha256)`; source rows are immutable. `input_sha256=SHA256(canonical_manifest_bytes)`.
   Include spec hash, original context/primary decision hash, membership ID and transformation versions in
   the manifest; exclude output IDs, wall-clock calculation duration and display-only labels.
8. Store a separate code revision and canonical complete-spec SHA256. Same inputs/spec/code reproduce the
   same advisory result/hash. A regenerated source row or changed normalization creates a new version; never
   rewrite a historical record. Hashes provide reproducibility, not proof that source claims are correct.

Minimum reason codes: INPUT_MISSING, INPUT_STALE, INPUT_AFTER_DECISION, INTERVAL_SEMANTICS_UNVERIFIED,
IDENTITY_CONFLICT, PRIMARY_SECURITY_BLOCK, BENCHMARK_VARIANCE_LOW, RESIDUAL_SCALE_LOW, BETA_UNSTABLE,
NO_RESIDUAL_CONTINUATION, RECENT_REVERSAL, NEGATIVE_BENCHMARK_RESIDUAL, PRE_EVENT_BASELINE_MISSING,
NO_REVERSAL_THESIS, ROUTE_NOT_COMPARABLE, DEPTH_LOSS, EXIT_ROUTE_UNAVAILABLE, COST_NOT_RECOVERED,
TARGET_HEADROOM_INSUFFICIENT, EVENT_EXPIRED, RESEARCH_BUDGET_UNAVAILABLE and SUPPORT_SETUP.

## Agreement and conflict

There is no council confidence percentage, averaged score, majority vote, learned voting weight or hidden
tie-breaker. SUPPORT+SUPPORT is two correlated observations, not independent evidence multiplication.
SUPPORT+OPPOSE is a displayed conflict; neither wins automatically. CAUTION/ABSTAIN never silently become
OPPOSE or support. Current primary outputs and safety controls remain byte-for-byte unchanged in every case.

For the first **offline, hypothetical** filtering comparison only, predeclare three alternative masks:
remove a primary entry on A's valid OPPOSE; remove on B's valid OPPOSE; remove on either valid OPPOSE. Keep
entries under SUPPORT, CAUTION or ABSTAIN. Test whether any benefit survives a missingness/coverage-matched
baseline. Do not add independent council entries or recycle freed slots in that first event-level experiment.
See [validation_plan.md](validation_plan.md) for the separate matched-capital account replay.

The primary may legitimately reject an unsafe candidate with both advisors showing a mathematical setup.
Security, STOP, guardian supervision, quote costs, account-relative capital caps and LIVE authorization have
priority under every future experiment. No advisor may clear a primary veto, promote another strategy,
change exit supervision, or authorize a larger position.

## Is the evidence actually new?

Log shared returns, SOL benchmark, pool source, liquidity checks, quote pair and regime dependencies. Evaluate
only matched-time observations when measuring agreement, score correlation and conditional loss correlation.
Report coverage denominators and uncertainty; do not code ABSTAIN as numeric zero. Cluster pools sharing a
mint and assets sharing a market shock. Stratify BRC against B2/H1/H3/H6 and ECDR against H2/H4/H7.

Required rejection question: after primary score, asset age, regime, liquidity, cost and opportunity timing
are matched, does the advisor add net value? If not, remove the advisor, even if it has a compelling story or
attractive standalone backtest. If primary decisions remain all NO_TRADE, all filtering arms remain identical
cash and incremental filtering efficacy is not identifiable. Preserve that result; fix/version the primary
data policy separately before a new baseline experiment.

## What learning may eventually mean

Read research to define hypotheses; use point-in-time development data to test/falsify them; freeze a version
before forward evaluation. Historical outcomes may estimate later uncertainty under registered expanding
windows, never retroactively change an advisory. No LLM or advisor output becomes a trading probability.
The existing local model remains zero-influence. Filtering, ranking and sizing each need a separately tested
role, explicit applicable capital limits, and their own future authorization; success in one does not qualify
another. Profit growth never automatically increases advisory influence or position size.
