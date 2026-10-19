# Two supplemental KESTREL advisors — research specification v1

Date: 2026-10-19. Audited source: `32344daf517c58f88f87e6a1eb21ba86f4fcb035`.
Status: **experimental design; SHADOW only; zero influence; no performance experiment performed**.

The selected advisors are **C1_BRC, benchmark-residual continuation**, and **C2_ECDR, execution-confirmed
dislocation reversal**. The first asks whether strength remains after removing a measured market exposure.
The second asks whether an unusually adverse move is followed by observable execution recovery without a
loss of usable liquidity. Neither is a validated memecoin strategy. ECDR is particularly speculative on young
tokens. Its complete absence of usable signals would be an acceptable research result.

These choices reflect an audit: H1–H6 already cover migration continuation, spike pullback, breakout, volume
pullback, retest and revival; B2 is simple momentum and H7 is exhaustion. BRC adds an explicit benchmark model
and held-out residual test; ECDR adds a pre-enrolled execution-cost/recovery sequence. They still share prices,
liquidity and market exposure with the primary engine. Economic differences do not establish statistical independence.

## 1. Evidence and scope

These are **our proposed adaptations**, not algorithms or parameters validated by the cited papers.

- [EQ1](equity_evidence.md): Moskowitz, Ooi and Pedersen (2012), §§2–5, Eqs1/5, pp230–244: slower continuation
  in liquid futures, with substantial shared information between momentum variants. Monthly evidence does
  not establish a 15-minute token effect. BRC is not a reproduction of their 12-month futures strategy.
- [EQ2](equity_evidence.md): Daniel and Moskowitz (2016), §4/Table5, pp232–233, §6/AppendixA pp242–243:
  important rebound/crash and cost limitations. The short-loser mechanism is not the payoff of a spot-only BUY.
- [EQ3 and V1–V2](equity_evidence.md), [counterevidence](validation_evidence.md): Moreira/Muir (2017), Eq1,
  §II.B/TableIV; Cederburg et al.(2020), §§3–4; Liu/Tang/Zhou (December2018 working version), §3:
  risk controls can help some portfolios, but in-sample improvement need not survive real-time estimation.
  No advisor uses uncapped inverse-variance leverage or interprets low observed volatility as extra buying power.
- [EQ4–EQ5](equity_evidence.md): de Groot/Huij/Zhou (accepted2011/published2012), §§4/6, PDFpp16–28;
  Cheng/Hameed/Subrahmanyam/Titman (2017), introduction and conclusion: universe, turnover, participation and
  costs matter for reversal. These sources do not prove that a memecoin dip is a temporary inventory shock.
- [Crypto C1](crypto_evidence.md): Tadi/Kortchemski (2021), §§3–7, pp6–22: a narrow shortable derivatives
  study, with a material rebate contribution. It motivates careful relative-price/reversion tests, not a
  transfer of reported returns to long-only AMMs. BRC does not assume cointegration or a tradable hedge.
- [Crypto C2/C6/C7](crypto_evidence.md): Uniswap v2 §§2/3 and PumpSwap state/fee documents establish why
  reserve semantics, routing and exact amounts matter. They establish mechanics, not an investment edge.

Read scopes, accessible copies, canonical links, sample periods, precise locators, contrary results and
unread sources are documented in the evidence files and commit-pinned source manifests. No source establishes
the proposed thresholds, an expected KESTREL return, or a probability of success.

## 2. Common information and execution contract

### Time and identity

`d` is the original decision time, not the later time when an offline advisor finishes. Each input must have
event interval end `<= d` **and** first response receipt/usable-at time `<= d`. Backfilled history downloaded
today cannot be inserted into yesterday's information set. Retain interval start/end, receipt time, raw hash,
parser/spec version, pool/venue, mint/permanent security ID, currency and price adjustments.

Use one venue/pool and one source lineage per instrument. Do not splice pools on migration, silently change
benchmark, join mutable display records, or forward-fill missing observations. Aggregate three verified,
contiguous closed5m OHLCV bars into one15m bar: first open, maximum high, minimum low, last close and sum of
**non-overlapping native bar volume**. Rolling snapshot volume cannot be summed into candle volume.

Provider bar-start semantics are still marked TO_VERIFY in this repository. Verify interval semantics and
base/quote identity before treating candles as advisor-ready. Use only aligned bars received by `d`; benchmark
and asset timestamps must match. A missing required bar resets the affected contiguous warm-up. Do not replace
fixed-interval volatility with the existing cadence-dependent snapshot volatility.

For equities use exchange sessions and permanent IDs, point-in-time corporate actions and delisting outcomes.
Known scheduled splits/earnings within one session of an ECDR event exclude it. A new action learned after
`d` cannot retrospectively make a historical trade vanish. Return-adjusted series and raw executable prices
have separate fields; no future corporate-action information enters a historical feature. Price-target anchors
must be translated to the same executable share/unit basis using only actions known by `d`. A known split or
cash distribution affecting the event/anchor is not a transient dislocation; exclude that ECDR event rather
than mixing adjusted total-return levels with raw tradable prices.

### Eligibility and universes

All profiles are spot/long-only research. No short hedge, margin or borrowing is assumed.

| Profile | Decision bars; benchmark | Initial universe rule, not an alpha claim | Execution limitation |
|---|---|---|---|
| `equity_daily` | Closed regular-session daily bars; SPY total return | Point-in-time top100US common shares by market cap, price >=$5, prior20session dollar ADV >=$25m, at least2years listing history | No equity adapter, adjusted universe, quotes, broker/fractional-share contract or commercial data rights currently implemented; execution stance ABSTAIN until supplied |
| `liquid_crypto_1h` | Closed UTC hourly bars; BTC/USD on the same permitted venue | Frozen top20spot universe by prior30day venue dollar volume, ADV >=$10m, observed listing >=30days; reported volume is not verified organic flow | Venue access, production-data rights, fees, lot sizes and contemporaneous depth must be established; historical research only meanwhile |
| `solana_meme_15m` | Closed UTC15m bars; separately normalized SOL/USDC | At most20pre-enrolled mature candidates, independently verified liquidity >=$100,000, first observed >=96h ago, existing security PASS and no identity/supply conflict | Only qualified constant-product pools for ECDR; no bonding curves, unsupported Token-2022 behavior, guessed reserve adjustments, or synthetic chart histories |

Freeze membership daily from information already available; keep admitted members, failures and outcomes
after they disappear. First observed age is a lower bound on age, not a verified creation timestamp.
The $100small-account replica and $100,000learning book remain separate. Sizing/risk supplies the intended
ticket; neither advisor calculates an allocation or increases a ticket.

### Exact-size cost qualification

For intended input capital `Q` in account-numeraire units, obtain a BUY quote yielding `a` token units and a
SELL quote for **that exact `a`** yielding `Q_back`. Keep request/response times and route identity for both.
The final pair must meet the existing10s freshness bound and a maximum5s inter-leg receipt separation.

`c_rt = (Q - Q_back + F_extra) / Q`

`F_extra` includes only costs not already embedded in quote amounts. Do not charge spread, pool fees, routing
fees or AMM impact twice. A same-time pair measures a present cost/routing diagnostic, not future realized
round-trip return. Negative costs, crossed/stale quotes, unexplained rebates or incoherent quote sequences
produce ABSTAIN until explained. Never call this a guaranteed arbitrage.

Qualification requires `0 <= c_rt <= min(existing risk limit, 0.03)` for the meme profile. Candidate ceilings
for equity/liquid-crypto research are0.005/0.01 respectively; these are conservative starting hypotheses,
not measured venue costs. Stress wider costs, route loss and adverse movement separately.

Realized modeled return in the $100SOL replica is
`(actual_or_modeled_exit_SOL - entry_SOL - nonembedded_entry/exit/failed_fees - irrecoverable_rent) / entry_SOL`.
Reserve refundable rent as capital occupancy and release it only under the stated accounting model; do not
silently treat it as certain immediate cash. A token's USD return or regression residual is not SOL profit.
For stocks use executable USD fills and supported lot sizes; fractional shares are not assumed available.

Missing cost evidence forces a would-be SUPPORT to ABSTAIN, even if a chart setup exists. Independently
complete negative-price evidence may produce OPPOSE with economics explicitly UNMEASURED; it does not claim
the profitability of an alternative trade. An observed failed sell route or failed cost condition can likewise
be known adverse evidence for OPPOSE. An unrequested quote is simply unknown. Preserve the mathematical
candidate signal separately so data availability is not confused with economic success.

## 3. C1_BRC — benchmark-residual continuation

### Hypothesis, horizon and equations

Persistent relative strength might add information beyond a rising common market. Removing one fitted
benchmark exposure could distinguish token-specific continuation from a SOL rally. A one-factor residual
is **model-relative**, not proof of idiosyncratic information or fair value. Sector, size, venue and ecosystem
factors remain omitted; abrupt beta changes are a major failure mode.

Let `r_i,j = ln(P_i,j/P_i,j-1)` and `r_b,j = ln(P_b,j/P_b,j-1)` on aligned closed intervals.
At latest completed bar `T`, use `N` training returns ending at `T-S-K`, `K` signal returns ending at `T-S`,
and exclude the most recent `S` returns. Training and signal windows do not overlap.

Fit ordinary least squares with intercept on training only:
`beta = sum((r_b-mean_b)*(r_i-mean_i)) / sum((r_b-mean_b)^2)`;
`alpha = mean_i - beta*mean_b`.

Training residuals `e_j = r_i,j - alpha - beta*r_b,j` give
`s = 1.4826 * median(abs(e_j - median(e)))`.
Signal statistic `Z = sum_signal(r_i - alpha - beta*r_b) / (s * sqrt(K))`.
`Z` is a diagnostic score; serial dependence and nonnormality mean it is **not** a t statistic or probability.

| Parameter | Equity daily | Liquid crypto1h | Solana meme15m |
|---|---:|---:|---:|
| Training returns N |252|336|288|
| Signal returns K |63|24|16|
| Skipped returns S |5|1|1|
| Minimum consecutive closed prices N+K+S+1 |321|362|306|
| Maximum hypothetical holding |20sessions|48h|8h|
| Maximum latest-bar lateness |1session|1h|15m|
| Minimum training benchmark stddev per bar |0.001|0.0005|0.0005|
| Minimum robust residual scale per bar |0.001|0.0005|0.001|
| Required beta range |[-1,3]|[-1,4]|[-1,4]|
| Maximum absolute beta difference between training halves |1|1.5|1.5|

The meme window needs305returns spanning76h15m and306closed15mprices; three5mbars per15mbar require918native
bars. A fresh200bar response is insufficient. Repeated genuinely received history must accumulate. All
stability, freshness and universe thresholds are predeclared design choices subject to future falsification.

### Setup, cancellation, vote and hypothetical exits

1. Enforce common identity/time/security conditions. Require all consecutive prices and nonsingular fit.
   A stale-price/zero-variance series, an out-of-range beta, or disagreement between half-sample betas => ABSTAIN.
2. `Z >= 2`: LONG candidate. Require the latest skipped interval's return in account numeraire nonnegative;
   otherwise CAUTION (`RECENT_REVERSAL`). A valid candidate with qualified current costs produces SUPPORT
   for a prospective long; missing costs produce ABSTAIN, measured cost failure produces OPPOSE.
3. `Z <= -2`: OPPOSE a prospective long (`NEGATIVE_BENCHMARK_RESIDUAL`). This is not a short order.
4. Between thresholds: CAUTION (`NO_RESIDUAL_CONTINUATION`). Insufficient inputs always take precedence.
5. A SUPPORT expires when the final quote expires or a new closed bar/safety event invalidates its input set.
   A separate isolated hypothesis-paper entry may occur only at the next executable observation after the
   decision, within one profile bar; missed or materially changed setups are cancelled rather than backfilled.
6. Hypothesis exits: close signal on `Z <= 0`, maximum holding time, supported8% adverse-price stop, or any
   applicable stricter primary/risk/guardian exit. These are desired exit conditions, never guaranteed fills.
   Primary-advisor-veto experiments keep primary exits unchanged to isolate filtering value.

There is no fixed expected-return estimate. Do not infer that `Z=2` pays a2%fee. Before any future influence,
matched forward outcomes must show incremental net value under the exact-size cost model. Prefer rare NO TRADE
over converting residual amplitude into invented expected profit.

### Illustrative arithmetic, not a backtest

For16signal bars, training `alpha=0.0002`, `beta=1.5`, `s=0.006`, observed token log return0.12 and SOL log
return0.04 give residual sum `0.12 - 1.5*0.04 - 16*0.0002 = 0.0568` and `Z=2.366666...`.
Assume the skipped bar is nonnegative, fit/identity/security gates pass and a fresh exact-size quote is present.
For `Q=0.1SOL`, `Q_back=0.098SOL`, `F_extra=0.0001SOL`, `c_rt=2.1%`, below the3%candidate ceiling.
This illustrates a SUPPORT record, not a predicted5.68%gain. Past token/SOL return here is `exp(0.12-0.04)-1`,
about8.33%; neither that past return nor the residual is a future forecast.

### Falsification and baselines

Compare with B2/raw same-window momentum, beta1relative momentum, and the same primary engine with no advisor.
Reject complexity if it does not improve paired net account outcomes, merely tracks SOL, or adds no information
after primary strategy, age, liquidity and existing regime controls. Other disproofs: unstable beta, apparent
results confined to one survivor cohort, signal decay before executable entry, or disappearance under modest
cost stress. A good standalone chart cannot establish incremental filtering value.

## 4. C2_ECDR — execution-confirmed dislocation reversal

### Hypothesis and extra data

A large adverse relative-price move followed by improving executable costs and preserved usable depth might
contain a temporary component. This is **not** identification of forced selling, inventory pressure or a
fundamental price. Equity reversal evidence includes contrary mechanism findings, and the available crypto
pairs paper depends on short legs/rebates. Long-only token recovery is a new, experimental hypothesis.

Only at most2pre-enrolled pools/instruments are eligible for the intensive observation panel. Enrollment must
precede the event; otherwise ABSTAIN. Historical baseline quotes cannot be requested after a shock and then
pretended contemporaneous. Keep a fixed intended-ticket identity and currency throughout a recovery event;
account growth/ticket changes reset baseline qualification rather than rescaling nonlinear impact.

| Parameter | Equity daily | Liquid crypto1h | Solana meme15m |
|---|---:|---:|---:|
| Prior relative-return observations N |60sessions|168hours|96bars|
| Volume baseline bars |20sessions|24hours|20bars|
| Maximum absolute benchmark event return |2%|2%|1%|
| Event standardized relative return |<=-3|<=-3|<=-3|
| Minimum reported native-bar volume / prior median |2|2|2|
| Recovery price confirmations |2next session closes|2next15m closes|2next5m closes|
| Minimum spacing of recovery quote sets |1session|15m|5m|
| Event entry expiry after event close |3sessions|2h|30m|
| Maximum holding after entry |3sessions|6h|90m|

Use `x_j = r_i,j - r_b,j` and the prior-window median `m` and MAD scale `s_x=1.4826median(abs(x-m))`,
excluding the event bar. Trigger only when `(x_event-m)/s_x <= -3`, benchmark move is within its table bound,
and reported native volume is at least2times its prior median. Reject zero scale; require the same scale
floors as BRC. Reported volume is corroborating context, not proof of organic demand. Do not infer distinct
traders or beneficial owners from transactions or volume.

### Depth and execution sequence

For supported **simple constant-product pools**, record properly normalized real reserves `X,Y`, program
version, decimals, fee/virtual-reserve state and block/slot evidence. Define `D=sqrt(X*Y)` only when that
invariant is appropriate. Require the minimum observed `D` during event/recovery to be at least95%of the
pre-event baseline; a sampling gap cannot certify no withdrawal. Baseline reserve observations must be within
5m before the event interval starts, ongoing samples at most5m apart, and the final reserve state <=60s old.
That sampling does not exclude an unobserved intrainterval pull; disclose its remaining risk.

Concentrated-liquidity ranges, virtual reserves, rebasing/transfer behavior and unsupported protocol upgrades
invalidate the simple formula. Do not substitute `2*quote_reserve*price` or USD TVL. Initially such cases ABSTAIN.
For future centralized stock/crypto adapters, use observed two-sided depth within50bps of the midpoint,
`D=min(bid_depth_USD,ask_depth_USD)`, with an80%pre-event floor; never apply AMM reserves to an order book.
Absent point-in-time depth (the current stock/centralized-crypto state) => ABSTAIN, not a chart-only live vote.

Require6valid same-ticket pre-event cost pairs: last90m at15m intervals for memes, last6h at1h intervals for
liquid crypto, last6sessions at closing observations for equity. Let `c0` be their median. The most recent
baseline must precede the event interval and be no older than one baseline interval. Zero/unknown baseline
cost cannot support a cost-spike ratio and produces ABSTAIN.

The15m meme baseline is a budget compromise, not evidence that costs are stable for15m. Two watched pools
consume16baseline quote requests/hour. A hard additional-research cap of30requests/rolling hour and2/minute
leaves at most14requests/hour for event/recovery/final pairs, including failures. Two complete3pair events
normally consume12of those requests; if additional fresh pairs cannot fit, ABSTAIN. Limit new panel event
attempts to2/hour and2per instrument/day. Never borrow reserved exit capacity. The first implementation
instead reuses existing measurements and adds zero network calls; unavailable sequences simply ABSTAIN.

An observed event cost `c_event >= 1.5*c0` must occur by one recovery interval after event close. Require two
subsequent quote sets, with event-to-first-recovery and first-to-second-recovery spacing each at least the
table interval, satisfying each
`c_recovery <= min(1.2*c0, 0.75*c_event, profile_cost_ceiling, existing_risk_limit)`.

Pool depth and route costs must describe the same economic venue/exposure. For DEX v1, the inspected quotes
must execute through the enrolled pool as the single economic route; an unexplained router/path change
invalidates a pool-level recovery claim. Record otherwise observable routed-cost changes separately but ABSTAIN
from this strategy. No new assumption that a pool invariant measures aggregate multi-hop Jupiter depth.

The second recovery pair can be the final pair only if still <=10s old at the original decision; otherwise
require a separate final fresh pair. The earlier pair is historical evidence only. Data obtained after a
primary decision can only inform a later decision; it cannot be attached retroactively to the earlier hash.

### Entry, vote, cancellation and exits

Require2increasing closed recovery prices: `P_event < P_rec1 < P_rec2 < P_pre`, and no depth/identity/security
invalidation. `P_pre` is the close immediately before the event. Also require a contemporaneous same-pool
reserve-implied reference price or same-venue L2 midpoint `P_ref`, received by the decision and no older than
60sfor the pool profile or10sfor an L2 profile. Require `P_rec2 <= P_ref < P_pre`; a new decline or gap through
the pre-event price cancels the current setup. A stale daily close cannot substitute for the next opening
price. The reference price is separate from the all-in BUY price so embedded entry costs are not charged twice.
Require a positive remaining log gap `g=ln(P_pre/P_ref)`.
The hypothetical target is `P_target=P_ref*exp(g/2)`; its gross target distance from the current reference is
`exp(g/2)-1`, not half a log return treated as an arithmetic percentage.

Require target distance `> c_final + m_cost`, where proposed conservative margins are0.2%equity,
0.5%liquid crypto and2%memes. This is only headroom to an assumed target, **not expected profit or a forecast
that the target will be reached**. An estimated transition probability may be added only in a separately
calibrated version with independent validation.

- Complete qualifying event/recovery with fresh final cost => SUPPORT a prospective long.
- Fully observed event with lost depth, failed sell route or costs still above the recovery threshold =>
  OPPOSE a prospective long; do not turn that into a short position.
- Fully observed eligible panel without a qualifying dislocation => CAUTION (`NO_REVERSAL_THESIS`).
- Missing baseline, missing recovery/venue/depth evidence, unsupported pool or unknown costs => ABSTAIN.
- Cancel after event expiry, a new extreme adverse low during confirmation, route/amount identity change,
  security failure, or insufficient target headroom. A later rebound does not resurrect the expired event.
- Hypothesis exits: executable half-gap target; stricter supported8%price stop; maximum hold; failure to
  make further recovery after2profile bars; or existing stronger risk/guardian exits. Count failed attempts
  and unresolved exposure. Stop/target/max-hold levels cannot guarantee liquidation or a maximum realized loss.
- In primary-filter ablations, preserve primary exits, holding rules and sizing. The above standalone exits
  belong to a separate hypothesis characterization; mixing them into a filter test would change the estimand.

### Illustrative arithmetic, not a trade prediction

Suppose a pre-enrolled meme pool has pre-event price1.00, event close0.90 and two5mrecovery closes0.91/0.92.
Benchmark event return is0%; prior median relative return0 and MAD scale0.025 give shock score
`ln(0.90)/0.025=-4.21442...`. Volume and all other gates are assumed satisfied for this arithmetic example.

At an illustrative constant SOL/USD price100, pre-event real reserves `X=1,000,000tokens,Y=10,000SOL`
imply token/USD1.00 and `D0=100,000`. Final reserves `X=1,000,000,Y=9,200SOL` imply token/USD0.92 and
`D/D0=sqrt(9,200,000,000)/100,000≈95.9166%`. Assume all intermediate samples also meet95%.
With `c0=1.5%`, `c_event=3.0%`, recovery costs1.8%and1.6%five minutes apart satisfy the1.8%recovery ceiling.
For the latest pair `Q=0.1SOL,Q_back=0.0985SOL,F_extra=0.0001SOL`, `c_final=1.6%`.
Assume the same final reserve state and SOL/USD conversion are fresh, identity-verified observations.
Target price `sqrt(0.92*1.00)=0.959166...`, gross distance4.2572%, exceeds1.6%+2%margin.
This can produce a SUPPORT record if all real inputs/clock/security gates pass. It does not predict that the
target will occur, prove a temporary cause, or imply4.2572%-1.6%will be realized. Later executable sell quotes
and failures determine the actual modeled outcome.

### Falsification and baselines

Compare with plain same-window relative-price reversal, the same event rule without execution recovery,
and unchanged primary/H2/H4/H7 entries. Reject this advisor if apparent benefits disappear after fee/turnover
accounting, depend on hindsight event selection or vanished losers, merely repeat an existing security veto,
or fail after matching liquidity/age/market state. A high recovery hit rate accompanied by rare permanent
losses or blocked exits is a failed strategy. Absence of reliable pre-event data is insufficient evidence,
not permission to loosen the sequence. Expected turnover is intentionally constrained: at most2events per
enrolled instrument per day and1active episode per instrument; no reentry into the same event.

## 5. Pseudocode and implementation boundary

```text
on_original_decision(primary_context, immutable_panel, spec):
    assert spec.mode == SHADOW and spec.influence == NONE
    select only inputs with event_end <= decision_at and first_available_at <= decision_at
    if common identity/time/security/universe conditions fail:
        emit ABSTAIN with explicit failures and any descriptive candidate statistic
    else if spec == C1_BRC:
        fit training-only beta/intercept; check variance, scale, stability and gap coverage
        compute held-out residual Z; require qualified exact-size economics for SUPPORT
        distinguish missing economics from independently measured adverse evidence; map to stated stance
    else if spec == C2_ECDR:
        reconstruct the pre-enrolled baseline and ordered shock/recovery sequence
        check invariant/depth applicability, quote route/amount identity, freshness and target headroom
        map complete evidence to SUPPORT/OPPOSE/CAUTION; missing evidence to ABSTAIN
    persist a separate immutable advisory with original decision hash and input/spec/code hashes
    assert original decision, risk inputs, sizing and signer behavior are unchanged
```

Machine-readable defaults and formula identifiers: [specifications.json](specifications.json).
Output contract: [advisory.schema.json](advisory.schema.json). Validation/ablation and promotion criteria:
[validation_plan.md](validation_plan.md). Laptop budget and future file-level work:
[implementation_plan.md](implementation_plan.md). Neither JSON file is installed application configuration.
