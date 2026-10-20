# KESTREL two-advisor validation plan

Status: research specification; no implementation or empirical performance result is asserted. Date: 2026-10-19. Read with [validation evidence](validation_evidence.md), which separates directly read primary counterevidence from proposed statistical methods. All numerical thresholds below are proposed design choices to register **before** examining their associated evaluation data. They are not literature-proven constants.

The council contains exactly two advisors:

- **C1_BRC — benchmark-residual continuation:** an advisor testing persistence after removing contemporaneously available benchmark movement.
- **C2_ECDR — execution-dislocation recovery:** an advisor testing reversal after a dislocation and observable recovery of executable liquidity.

Both start in **SHADOW with zero influence**. The council does not increase exposure, originate production entries, change exits, alter sizing, override a veto or enable LIVE trading. A prediction score is not a calibrated probability.

## 1. Present baseline and the question this experiment can answer

The audited ordinary discovery/strategy path currently reaches the **H7 veto** and can produce an all-NO-TRADE baseline. Preserve that behavior during research. Do not reinterpret H7, repair it as part of this experiment, or present a different manually selected path as the unchanged primary engine.

A filter cannot improve realized trading PnL when its frozen primary baseline takes no trades. In this condition the four policy arms below can be exercised for software correctness, coverage and record reproducibility, but they cannot establish incremental filtering value. Advisor-only hypothetical entries are a distinct research question and must not be smuggled into a primary-filter comparison.

The first statistical promotion experiment starts only after a separately authorized, versioned primary strategy configuration generates a nondegenerate eligible opportunity tape and is frozen. If that never occurs, report **BLOCKED: PRIMARY BASELINE DEGENERATE**, continue SHADOW evidence collection, and do not claim four-arm performance proof.

Primary question once that prerequisite holds:

> On the same primary opportunities, capital budget and execution assumptions, does filtering with either or both advisors improve daily account returns after costs by more than a preregistered economically useful margin?

Standalone advisor expectancy, future ranking, future sizing and independently generated entry opportunities require separate registrations and evidence. Neither a council gain nor passing this plan substitutes for the existing 30-day regime-calibration requirement, timed operational checks, production authorization or genuine forward requirements elsewhere in KESTREL.

## 2. Freeze the experiment and the information boundary

Before its validation period begins, write an immutable experiment registration with:

1. Primary version/configuration hash, advisor versions, complete parameter defaults, eligible instruments, pool-selection policy and asset-class profile.
2. The original primary decision and all vetoes, candidate/pool/mint identifiers, opportunity time, input identifiers and input hash.
3. Provider event time, request time, response receive time and feature availability time where recorded. If a field is unknown, mark it unknown. Do not equate provider event time with when the laptop received it.
4. Exact filter masks, cost model, fee-denomination conversions, ticket/account limits, position-conflict rules, observation-to-entry delay and outcome definitions.
5. Training/validation/final-holdout calendar boundaries; purge/embargo rules; fixed review dates; bootstrap and multiplicity procedures; economic/risk hurdles; permitted stopping rules.
6. Every attempted specification and variant, including rejected ideas and experiments with no trades. Append amendments; never overwrite the search history.

The frozen tape includes every primary opportunity, even those either advisor rejects or cannot assess. Advisor inputs may consume only observations actually available by that decision time. A response fetched later cannot retrospectively establish a historical tradable quote. Corporate-action adjustments and equity delisting records need their own availability and revision metadata; a modern survivor-only list cannot reconstruct the historical eligible universe.

At startup, features are computed from the existing collected observations without changing collector cadence. A sleeping laptop supplies no observations during its sleep. No interpolated candle, carried-forward exit quote, or retrospective source response may fill that evidence gap as if it had been observed.

## 3. Four arms and matched capital

The first evaluation is an **offline counterfactual experiment**, not a change to running strategy behavior.

| Arm | Definition on the frozen primary opportunity tape |
| --- | --- |
| P | Unchanged primary decisions, subject to all existing eligibility and safety gates. |
| P+A | P, with entries removed only by the registered C1_BRC filter. |
| P+B | P, with entries removed only by the registered C2_ECDR filter. |
| P+A+B | P, with entries removed by either registered filter; no majority vote or score averaging. |

For the initial conservative filter experiment, **SUPPORT, CAUTION and ABSTAIN preserve the primary decision; OPPOSE removes it**. No fresh actionable record means ABSTAIN. This mask is proposed and must be frozen before outcomes are inspected. A later proposal to treat CAUTION or ABSTAIN as a veto is an additional tested variant, not a silent improvement.

Perform two distinct analyses:

1. **Opportunity-matched diagnostic:** apply the four fixed masks to the same primary trades. A removed trade is replaced by cash, and freed capital is not silently reused. This isolates the filter's trade-selection effect. Show return per original opportunity, rejected-winner cost, avoided-loss benefit and retained coverage. Conditional return per retained trade is secondary and can look better merely by trading less.
2. **Matched-capital simulation:** independently run all four policy arms from a **$500 account replica with at most two concurrent positions**, identical original primary ticket sizing, account-relative limits, execution/cost model and deterministic conflict/order rules. Each arm maintains its own cash and realized/marked exposures. Freed cash can fund a later original primary opportunity only under the same frozen simulator rules. No arm borrows the learning book's cash, uses a more aggressive ticket or increases its allowed exposure.

The $100,000 PAPER learning book is a separate descriptive experiment. Report its results separately; never net its gains, fills or trade count against the $500 replica. A notional $100 trade in a deep simulated book does not prove that the $500 account can pay its costs or keep two positions safely funded.

Report cash and uninvested time. Compare both with a **zero-return NO-TRADE/cash benchmark** and with simple preregistered filters such as freshness-only and a single benchmark-trend rule. Do not optimize a cash benchmark's return or assume interest the system cannot earn. Cash in SOL must also disclose its USD mark-to-market risk; present SOL-account and USD-account results consistently rather than treating SOL cash as a guaranteed zero USD return.

## 4. Outcomes, attrition and execution accounting

### Primary account estimand

Use paired daily account net returns on a common calendar:

`d[j,t] = r[arm_j,t] - r[P,t]`, for `j ∈ {A, B, A+B}`.

Daily returns use equity marked at the same boundary and a fixed quote/numeraire policy. For crypto the default reporting day is UTC; user-facing reports can additionally label America/New_York time. Equities retain the exchange calendar/session identity, while account records map to a common calendar for cross-market reporting. Cash-flow adjustments must be explicit. Do not compare different days only because one policy traded and another did not.

For missing or unexecutable marks, carry an **unresolved exposure flag**, publish feasible valuation bounds, and include a zero-recovery stress case. Marked PnL, conservative executable liquidation value and settled cash are distinct series. No adviser receives credit for a chart-price exit that could not be executed. A policy claim that depends on treating unknown exits favorably fails the conservative evaluation.

### Required secondary reports

- Net trade expectancy, time-weighted account return, maximum drawdown, worst day, downside tail quantiles/expected shortfall where enough observations exist, turnover and exposure.
- Realized and unresolved losses; fees and failed-attempt costs; quote availability, delay, size-dependent impact and capacity surfaces.
- Coverage and attrition waterfall: discovered → primary eligible → fully timestamped → advisor warmed → valid advisory → executable entry → executable exit → fully scored. Include counts/reasons at every step.
- Original opportunities, retained/rejected opportunities, observed winners rejected, losses avoided and unknown outcomes for every arm. A table with no assessable cases must say so.
- Evidence counts in their actual units: tokens, pools, issuers, trades, days, block equivalents and effective sample sizes. Repeated polls are not new trades or independent evidence.
- Matched-coverage diagnostics: compare each advisor with a deterministic or seeded selection baseline retaining the same fraction by liquidity/age/regime. This helps separate useful information from mechanically reducing exposure. This is a registered diagnostic, not permission to choose the most flattering comparison.

### Realistic cost construction

Start from executable, size-matched buy and sell quotes at their actual available times. Quote exchange rates already incorporate some spread and AMM/route impact; identify these components before adding a fee or impact model to prevent double counting. Separately model costs not already embedded: chain/base/priority fees, route or platform fees, account-creation/rent cash lock where applicable, adverse price movement during observed quote-to-fill delay and failed attempts. Use actual fee schedules and recorded observations when available; unknown costs receive explicit conservative scenarios.

Keep dead tokens, withdrawn liquidity, stopped feeds, migrated pools and invalidated mints in the population. Join pool changes through asset identity, but never splice unrelated pools into a fictitious continuous execution route. Document issuer/shared-control clusters where known and acknowledge that hidden common control cannot be fully resolved from free data.

Stress at least higher adverse selection, higher failed-attempt rates, delayed quotes, a disappearing exit route, laptop sleep/network outage and simultaneous exits in shared liquidity. A stop threshold is a trigger, not a guarantee of its execution price. Results must be robust to a useful range of these assumptions; the range itself must not be chosen after seeing which one wins.

## 5. Chronology, purging and untouched evaluation

Use chronological development and walk-forward evaluation, with transforms and calibration fit only on training observations. Training-window lengths depend on available density and asset profile; register them without optimizing final-period returns. Do not shuffle individual candles or trades into ordinary random train/test splits.

The maximum outcome/holding intervals registered for this proposal are:

| Profile | Maximum label/holding horizon | Minimum temporal embargo around adjacent train/evaluation boundaries |
| --- | --- | --- |
| Memecoin | 8 hours | At least 8 hours, plus actual information delay. |
| Established liquid crypto | 48 hours | At least 48 hours, plus actual information delay. |
| Equity | 20 exchange sessions | At least 20 exchange sessions, plus actual information delay. |

These are maximum validation horizons for the proposed profiles, not a reason to extend a strategy's defined exit. If an outcome needs a later observation, extend the label interval and embargo; never truncate a difficult exit to keep the original boundary.

Each event label spans decision time through its last outcome observation. Purge training labels whose intervals overlap evaluation labels. Apply these exclusions across rows for the same asset, migrated/shared pool and known issuer/control cluster, not merely the one selected price series. A boundary must exclude later information used in fitted preprocessing as well as the explicit outcome. Shared market dependence remains and is handled by joint temporal inference below.

Reserve a final untouched period of **at least 60 calendar days** after all specifications and promotion criteria are frozen. Require at least **300 closed primary trades and an estimated effective trade-outcome sample size of 150** as additional minimum evidence floors. Explain the trade-level ESS estimator and report its sensitivity. These floors are policy proposals, not automatic proof or a power calculation; the result can remain insufficient after every floor is met. Equity 20-session labels require extra time after the last entry to mature, so the final observation date can extend beyond the entry-window endpoint.

Paired daily return ESS and usable joint calendar-block counts are separate quantities and are not replaced by the trade-level floors. A 60-day interval cannot magically provide 150 independent daily returns. If precision requires more days, extend prospectively under the preregistered rule or begin a new experiment. Do not relabel an inspected holdout as untouched. Hide council-arm performance summaries for the sealed period until its registered analysis; ordinary operational telemetry may remain visible.

## 6. Dependence-aware inference and multiple testing

Primary confirmatory family: the **three incremental comparisons** P+A versus P, P+B versus P, and P+A+B versus P. Include every attempted confirmatory parameter/mask/universe variant in the search family or give it a separately budgeted prospective error allocation. A renamed experiment does not erase previous tests. Report all tried variants, including failures and zero-trade results.

For each arm, test the one-sided economic null `H0_j: E[d[j,t]] <= δ_min_day`, with family-wise error controlled at **5% using Holm step-down adjustment** across the registered family. Publish both raw and adjusted values. Use multiplicity-adjusted one-sided confidence bounds obtained by inversion of the declared testing procedure; do not describe an unadjusted 95% bound as a family-wise bound. If a valid implementation is unavailable, withhold promotion rather than substituting an ordinary t-test.

Estimate uncertainty by resampling **joint calendar blocks**: the same selected time blocks and all their asset/policy observations travel together. Do not bootstrap each advisor, token or policy independently and destroy shared shocks. A moving or stationary block bootstrap is a proposed method; preregister its exact implementation, studentization/null construction, seed, number of replicates and failure handling before evaluation. Validate inferential behavior on simulations with known dependence as a method check, while clearly separating that check from trading evidence.

Choose block length from training-only dependence and clustered-overlap diagnostics, with a minimum of **two calendar days**, and lengthen it to cover longer relevant dependence/label horizons. Thus 48-hour crypto outcomes need at least their horizon; 20-session equity labels require blocks covering those exchange sessions on the calendar. Report a prespecified sensitivity range around the selected length. A small block count, unstable estimate or materially divergent block-length results means **INSUFFICIENT EVIDENCE**. The minimum two days is not a claim that crypto shocks cease after 48 hours.

### Economic hurdle

Proposed daily monetary hurdle:

`H_day = max(0.0005 × E_begin_day, incremental_operating_cost_day)`.

The corresponding return hurdle is `δ_min_day = H_day / E_begin_day`, using a consistent account equity definition and numeraire. Five basis points per day is an **unvalidated design hurdle**, not an expected return. Register or revise it before outcomes are examined. For time-varying cost/equity hurdles, test the centered paired series `d[j,t] - δ_min_day[t]` rather than quietly using a lower average hurdle selected later.

Incremental trading costs already deducted from arm returns must not be charged again as operating costs. Additional API/server charges are currently $0 by budget, but laptop CPU/energy, network use and operator overhead have not yet been measured. Meter CPU time, wall time, memory, requests and energy where a usable local meter exists; document the operator's cost convention. Unknown operating cost cannot be silently declared zero to pass this gate. Show a cost sensitivity table and label promotion blocked until the chosen convention and budget are registered.

### Power illustration, not a sample-size promise

For an idealized independent Gaussian paired-return mean, approximate effective sample size is:

`n_eff ≈ [(z_(1-alpha*) + z_(power)) × sigma_d / Delta]^2`.

Illustration: a conservative first Holm threshold for three comparisons has `alpha* = 0.05/3`, so `z_(1-alpha*) ≈ 2.13`. At 80% power, `z_(power) ≈ 0.84`. If the true improvement **above the economic hurdle** is `Delta = 0.02` and paired daily standard deviation is `sigma_d = 0.10`, then:

`n_eff ≈ [(2.13 + 0.84) × 0.10 / 0.02]^2 ≈ 221` effective observations.

The chosen 2% improvement and 10% standard deviation are illustrative, not measured KESTREL values or forecasts. Dependence, unequal exposures, fat tails, estimated variance, additional variants, missing outcomes and non-Gaussian inference can require substantially more calendar observations. Do not translate 221 into “221 trades” when the modeled observations are paired daily returns. Estimate uncertainty from development data and reassess feasibility before committing to a duration.

## 7. Promotion, demotion and drift

Promotion is specific to an advisor version, asset profile and role. The first possible role is a bounded **PAPER filtering trial**. It requires a nondegenerate frozen primary baseline, passing data-quality/coverage checks, a valid untouched evaluation, a multiplicity-adjusted lower bound above the economic hurdle, acceptable account-relative drawdown/tail risk under registered limits, and conservative cost/exit stress survival. Adequate operational reliability and current primary risk gates remain prerequisites. A successful research report alone does not turn influence on.

Ranking and position sizing require their own hypotheses, experiment families, matched capital comparisons and prospective evidence. Do not infer that a filter's avoided losses prove that its SUPPORT state deserves a larger trade. Profit alone cannot authorize growth.

Daily monitoring is descriptive: freshness, input distribution drift, coverage, computation cost, advisor disagreement, unresolved marks and observed risk breaches. Immediate deterministic safety conditions can freeze an advisor to zero influence: stale/invalid inputs, version/hash inconsistency, missing route, risk limit breach or failed process health. Such a freeze is not a statistical claim that the edge disappeared.

Formal performance/drift reassessment occurs only on a preregistered calendar or through a specified sequential method with a declared alpha-spending/e-value design. Do not run a fresh ordinary significance test every day and treat each as a new 5% chance. Before any influence is enabled, document the review schedule, multiplicity across initial promotion and later reviews, minimum block count, control limits and recovery criteria. If these are not specified, stay at zero influence.

If forecasts are later calibrated, fit and test calibration separately for memecoins, established crypto and equities, including horizon and applicable regime. Preserve a held-out calibration assessment. Current SUPPORT/CAUTION/OPPOSE states, raw signals and research scores have no calibrated probability interpretation. Instrument-specific invalidation cannot be erased by pooling it with a better-performing market.

## 8. Minimum useful first version and acceptance

1. **Immediately feasible:** an append-only registration and advisory tape using existing timestamped records, version/input hashes, reason-coded ABSTAIN and summaries of information coverage. No new API requests or runtime decision influence are necessary.
2. **Next:** an offline mask evaluator with the four exact arms, all-NO-TRADE detection, attrition reports, paired-calendar alignment and conservative unresolved-exit treatment.
3. **Then:** the separate matched-capital $500/max-two-position simulator, with one frozen execution/accounting model shared by all arms. Validate reconciliation and no capital borrowing across books.
4. **After sufficient real observations:** chronological evaluation, dependent inference, registered multiplicity and the sealed final holdout. A missing primary baseline or inadequate free-data history remains an explicit blocker.

Meaningful checks for this future implementation:

- Later input arrival changes only later decisions; replay cannot consume a future response.
- Duplicate pool observations and known shared issuer rows do not inflate evidence counts; overlapping labels are purged across their linked identifiers.
- Missing/sleep intervals produce abstention and missingness, not synthetic candles. Dead tokens and failed exits remain in outcomes.
- Both quote-embedded fees and separately reported fees are reconciled without double counting; delayed/unavailable exits cannot book chart-price cash.
- Four arms on an all-NO-TRADE tape report a degenerate baseline, not a profitable filter.
- A removed trade is cash in the opportunity diagnostic; reused cash occurs only in the separately labeled capital simulator.
- Capital replicas use identical initial capital, risk limits, original opportunity ordering and maximum two concurrent positions. The learning book cannot fund a replica.
- Reproducing the same registration, input tape and random seed reproduces the report and input hashes.
- A deliberately dependent synthetic dataset tests the inference machinery and multiplicity bookkeeping; its result is labeled synthetic and cannot promote an advisor.
- The report can conclude **INSUFFICIENT EVIDENCE**, **BLOCKED**, or **NO ECONOMIC IMPROVEMENT** even when a chart, nominal win rate or simulated learning-book balance looks good.

This research pass does not modify the running collector, existing trading rules, PAPER sizing or LIVE authorization. The remaining work is an implementable measurement plan followed by actual future observations; the latter cannot be manufactured by adding indicators or running synthetic tests.
