# Earlier opportunities: research findings and delivered first version

**Conclusion:** KESTREL first needs to measure opportunities it currently never qualifies. Adding another
indicator to its qualified set cannot repair that blind spot. Implemented here: a separate, deterministic,
record-only opportunity lab for **Robinhood first**, with a separate Solana track. Its leading hypothesis is
participation increasing before a large price extension; its simpler control is price movement alone.
Neither has a demonstrated net edge. No new entry, exit, confidence vote or automatic risk increase is enabled.

The original evidence council still has exactly two advisors: residual persistence and experimental
liquidity-shock recovery. See [the council design](../council/README.md). The new probes are measurement
experiments, not extra independent council members. Participation and momentum may simply encode the same
market shock. Their incremental information and correlated errors remain to be established.

Audit baseline: Claude `9c255ff` on `claude/funny-rubin-gq99vj`, including prior Codex `63e24e4` and Claude's
learning lab. No AGENTS.md found. Read project planning/progress/acceptance/handoff and relevant code/tests;
source behavior, rather than a milestone's name, determines claims below. Research pass: 2026-10-20.

## The opportunity and its present bottlenecks

The objective is a subset of spot opportunities identifiable from information received **before** an
executable move, whose complete entry-to-exit distribution improves on cash, comparable BTC exposure and
the unchanged primary engine after costs. It is not the ability to label every eventual 500% chart winner.
“Early” means actionable time before the primary or price-only rule at the same ticket, not time before
an eventual peak selected with hindsight. Merely downloading a historical chart does not establish that time.

| Consequential finding | Source path / implication |
|---|---|
| Robinhood's API-listed USD instruments are its executable universe. A Solana token can be visible elsewhere and unavailable here. | `rh/history.py:universe`, `rh/engine.py:refresh_pairs`; preserve venue membership events and missing-history assets. Laptop doctor previously reported 90 tradable of 92 pairs; this cloud pass did not verify current credentials or repeat that provider call. |
| Current production multi-frame rules require 100 consecutive daily closes, 60 complete 4h bars, 61 hourly and 61 quarter-hour bars. | `rh/multiframe.py`; the history floor excludes young listings. Missing Coinbase intervals and symbols unsupported by Coinbase also prevent qualification. The operator screenshot illustrates this, plus a median daily volume floor of $1m. |
| More bars do not require more trades. | Existing free Coinbase collection already gathers 15m/1h/1d history and derives 4h bars. The new RH research uses only five contiguous closed 15m bars, without changing production's floor or filling any gaps. |
| Robinhood learning already explores trend, cross-sectional momentum, reversal, timing and a logistic model. | `rh/learn.py`; repeating RSI/EMA variants adds little independent evidence. The grid includes timing alternatives, so “255 variants” is not a safe universal current count; read the versioned candidate count from each run. |
| Historical learning is ahead of validation. | Today's listed universe omits previously dead/delisted names; recent/assumed costs are projected backward; a final untouched outer holdout is absent. Trade-level bootstrap and per-fold capacity checks deserve a joint-calendar portfolio audit. A passed historical PAPER gate is not production alpha evidence. |
| Solana discovery stops many names before strategy evaluation. | `discovery/funnel.py`, default thresholds: pool age >=15m, liquidity >=$40k, h1 volume >=$20k, FDV $100k–$50m, >=100 distinct traders, freshness <=120s; tracked set <=300, two deep assessments/cycle. These mix security necessities with research opportunity assumptions. Keep them intact while measuring exclusions. |
| Rejected-only follow-up is an incomplete denominator. | Existing `discovery/counterfactual.py` follows first rejections. A later same-dollar round trip is not the exit of the original acquired quantity. The new lab keeps all locally observed pool identities, UNASSESSED/DEFER outcomes, missing exits, and exact-quantity quote diagnostics. Existing counterfactual code is unchanged. |
| $100 has been superseded by $500 for the reference account. | Both config sections now use $500; normal 10% PAPER size starts near $50, a 15% ceiling near $75. Learning cash stays $100,000. Original books, actual historical positions and cash journals are retained; Solana reference changes are append-only events at the original recorded SOL/USD conversion rate. This changes sizing units, not evidence quality. |

The screenshot's unavailable Coinbase history cannot be fixed by trusting a Robinhood quote as an OHLCV
series. A present quote gives neither past volume nor a complete intrabar path. Existing missing bars stay
missing. Some gaps may reflect genuine no-trade periods, others absent responses; we cannot distinguish all
of them from a REST omission alone. Do not silently turn either into a flat candle.

## Research evidence: what was actually read

All eight documents below were downloaded from accessible public repositories and substantive portions read
in this pass. **Read scope is partial**, including methods/data/cost sections where stated, not “full paper”
merely because a complete PDF was downloaded. The [source manifest](sources.json) records exact links,
download hashes, versions and page locators. PDF page numbers below count from the first page; manuscripts'
printed numbering may differ. No paper's empirical experiment was independently replicated here.

| Primary source | Supporting locator and tested setting | Finding and transfer limitation |
|---|---|---|
| Moskowitz, Ooi & Pedersen (2012), [*Time series momentum*](https://raw.githubusercontent.com/xhlgogo/Quantitative-Investment-Trading-system/master/paper/10.Time%20Series%20Momentum.pdf) | §§2–3; PDF pp3–8, Tables 1–2. 58 liquid futures across equity indices, currencies, commodities and bonds; main common sample Jan1985–Dec2009, some series begin1965. | Persistence at monthly horizons motivates continuation research. Long/short diversified futures, volatility scaling, collateral and roll mechanics differ from a new spot pool. The inspected sections do not provide a transferable fee/fill model; no net KESTREL expectancy is inferred. |
| Daniel & Moskowitz (2016), [*Momentum crashes*](https://raw.githubusercontent.com/xhlgogo/Quantitative-Investment-Trading-system/master/paper/4.Momentum%20Crashes.pdf) | §2.1–2.3, Fig1/Table1, PDF pp3–4; regime discussion pp2,8. US CRSP equities Jan1927–Mar2013; monthly rebalanced long/short momentum. Fig1 explicitly assumes no costs. | Rebounds after distressed markets can hurt momentum portfolios. Much crash exposure involves the short loser leg; spot-only KESTREL does not inherit that exact payoff. Risk-state conditioning is a hypothesis, not a profitable crash predictor proved here. |
| de Groot, Huij & Zhou (2012 publication; **July2011 accepted manuscript**), [*Another look at trading costs and short-term reversal profits*](https://raw.githubusercontent.com/kuzeysahin-ai/quant-paper-pipeline.2/main/papers/paper-02-groot-huij-zhou-2012/source.pdf) | §2, §4.1–4.2, §5, AppendixB; PDF pp8–10,16–23,31+. US largest1500,1990–2009; Europe largest1000,1995–2009; weekly-return sorts, daily rebalancing and one-day skip; institutional cost models. | Liquidity selection and turnover control matter. Excludes microcaps; broad-universe costs can consume reversal returns. Their discussion challenges a simple inventory-liquidity explanation, so it does not prove that a meme dip is temporary. Unfilled-trade opportunity costs are omitted by that cost model. |
| Cederburg, O'Doherty, Wang & Yan (2020), [*On the performance of volatility-managed portfolios*](https://raw.githubusercontent.com/coolgan/Volatility-Managed-Portfolios-Sometimes-it-Works/057ac211dcbdc8564a81f626a59cdffa13322229/reference/%E3%80%90Cederburg%2C%202020%2C%20JFE%E3%80%91On%20the%20performance%20of%20volatility-managed%20portfolios.pdf) | §§2–4, Tables1–2,6,9 and earlier project notes in `research/council/validation_evidence.md`. 103 equity strategies; factor starts vary from1926 onward, sample ends2016. | Volatility management does not systematically dominate directly, and implementable out-of-sample combinations can disappoint because of instability. Contradicts assuming a risk-scaling improvement is automatic. Factor portfolios and their cost/normalization conventions are not spot execution assumptions. |
| Tadi & Kortchemski (2021 working paper), [*Evaluation of Dynamic Cointegration-Based Pairs Trading Strategy in the Cryptocurrency Market*](https://raw.githubusercontent.com/jackywong1228/CTA-Quant-Papers/0c30ae8c0cdb2cc671f4a19a277633462f21cc2b/73_Cointegration_Pairs_Trading_Crypto_Tadi2021.pdf) | TableI, §§3–4, §7–8, PDF pp6–8,19–22,26–27. Nine BitMEX derivative families, Sept2018–Oct2019; three-month formation/one-week trading,39 weeks. | Minute-level data and available size matter; reported maker rebates contribute materially to profits. Derivative shorts, funding, rebate economics and single-venue queue assumptions do not transfer to Robinhood/AMM spot takers. Reject pairs trading for this first version. |
| Adams, Zinsmeister & Robinson (2020), [*Uniswap v2 Core*](https://raw.githubusercontent.com/Uniswap/docs/1c7597d73be0e9a3a4e44aa75b8fb212b40ea136/archive/static/whitepaper.pdf) | §2.1–2.2, Eqs1–3, §3.2.1, Eqs9–11; PDF pp2–4,7. Technical protocol whitepaper; no alpha sample. | Marginal reserve prices differ from finite-size execution; routing and manipulative ordering affect costs. Historical30bp parameter is not a Solana or Robinhood default. Supports quote discipline, not a trading edge. |
| Li & Ferreira (2025 working paper), [*Follow the Leader: Enhancing Systematic Trend-Following Using Network Momentum*](https://raw.githubusercontent.com/jackywong1228/CTA-Quant-Papers/0c30ae8c0cdb2cc671f4a19a277633462f21cc2b/16_Follow_the_Leader_Trend_Li2025.pdf) | §4.1, §5.2, PDF pp6,8–9. 28 futures, data June2002–June2024; OOS Jan2005–June2024; stationary block resampling, half-spread-based cost convention. | Cross-market lag relationships are worth investigating, but synchronized continuous futures differ from thin spot coins. Timing, rolling history and chosen half-spreads can dominate apparent lead-lag. No claimed intraday crypto replication; defer graph fitting. |
| Wood, Roberts & Zohren (2021 v3), [*Slow Momentum with Fast Reversion: A Trading Strategy Using Deep Learning and Changepoint Detection*](https://raw.githubusercontent.com/jackywong1228/CTA-Quant-Papers/0c30ae8c0cdb2cc671f4a19a277633462f21cc2b/21_Slow_Momentum_Fast_Reversion_Wood2021.pdf) | Online CPD discussion, §IV, AppendixC/hyperparameter discussion; PDF pp4,6–7,13. Futures backtest1995–2020, chronological retraining. | Sequential regime changes motivate testing stable vs transient activity. Main evaluated objective omits costs at that stage; appendix discusses them. LSTM/GP tuning and multiple lookbacks add selection risk. No neural decision path is added; simple lagged medians are an intentionally falsifiable first probe. |

Primary leads including Liu–Tsyvinski cryptocurrency risk/factors, Makarov–Schoar arbitrage, Cong and
coauthors' wash trading, and backtest-overfitting/deflated-Sharpe papers were not verified in this pass.
No results are attributed to those unread sources. The old council manifest records the same access gap.
Direct arXiv remained proxy-denied after an environment draft was saved; no bypass was used. Full market
impact, manipulation and sequential-inference literature review remains incomplete. “Institutional” scrutiny
means stating this boundary, not inventing a literature consensus or 300 independent researchers.

## Mechanisms considered and disciplined selection

| Mechanism / potential observable precursor | Competing explanation and falsification | Decision under these constraints |
|---|---|---|
| Gradual participation expansion before price extension | Wash trades, batch reporting, common-market pump or signal appearing after most gains. Fails if lagged participation adds no incremental net selection beyond price/BTC. | **Selected for measurement**, distinct definitions per venue below. No paper above validates this exact predicate. |
| Common-factor-residual persistence | Beta changes, stale benchmark or asynchronous venue closes can create residuals. Fails if excess vanishes after point-in-time synchronization/costs. | Existing council advisor, not duplicated by adding another RSI rule. |
| Liquidity withdrawal followed by genuine recovery | Rug pull, unlocked supply and permanent repricing can mimic a dip. Fails if recovery lacks executable exits or harmful false positives dominate. | Existing experimental council advisor; insufficient reserve/quote history to justify trading. |
| Slow information diffusion / network leadership | Common news or nonsynchronous observations look like lead-lag; fitting all pairwise graphs amplifies testing. | Defer fitting; start with timestamped BTC comparison and test incremental value. No claim that BTC is the causal leader. |
| Migration, new listing or new venue access expands the buyer set | Migration can drain the old pool; listing can be announced/fully priced before receipt. | Log identity/access chronology now; keep security. Future event study requires reliable announcement, supply and venue timestamps absent today. |
| Sequential changepoint in participation, volatility or spread | Provider outage/resumption or changed aggregation can be the changepoint. | Fixed short-window probe first; formal online change detector requires calibrated false-alarm/availability tests before fitting. |
| Short-term reversal / liquidity provision | Costly spread bounce or new information makes the loser keep falling. | Existing pullback overlaps; no new reversal rule without incremental recovery evidence and full costs. |
| Volatility targeting / account risk adaptation | Quiet stale prices imply spuriously low volatility; unstable risk-return relation; caps change payoff. | Risk experiment only, no automatic leverage or profit-triggered increase. Counterevidence V1 matters. |
| Maker rebates, pairs, funding/carry or cross-venue arbitrage | Queue/adverse-selection risk, borrow/funding and transfer delays; user's venue may not allow the legs. | Reject for first version: spot-only access, $0 infrastructure, no verified borrow/short implementation. |
| Wallet/developer networks and beneficial-owner concentration | Sybil wallets, identity errors, sold activity and stale labels. | Security inputs remain useful; a predictive network experiment is blocked by reliable historical identities and free call budget. |
| Attention/news/social propagation | Bought engagement, archive edits and unknown publication latency. | Defer until a lawful free timestamped source is verified; no LLM judge or fabricated sentiment feed. |
| Latency sniping/order-book imbalance | Stale home-Wi-Fi observations lose the race; no guaranteed full depth here. | Reject latency dependence. Minutes-to-hours research; no GPU or permanent server assumption. |

Stock-session boundaries, auctions, splits/dividends, delistings and overnight gaps differ from crypto's
24/7 clock. Crypto still has venue outages, liquidity cycles and listings/delistings. Use UTC event/receipt
times, rolling windows and explicit gaps. Young tokens also change supply, pools and holders; do not merge
migration pools or retickered symbols into an uninterrupted return series. Spot-only means cash when bearish,
not an invented short position. Crossed Robinhood partner BBOs are observations; the mid is a diagnostic
reference, not a guarantee one account can buy the low ask and immediately sell the high bid.

## Exact first-version hypotheses

Machine defaults are frozen in [specifications.json](specifications.json), registered by hash in research.db,
and copied into every experiment registration. Inputs are frozen with a reproducible SHA-256. A WATCH is
uncalibrated evidence, not a probability, an approved trade, or independent agreement with the primary engine.

**Robinhood early participation.** At the actual local first receipt `t`, take the latest five contiguous
closed Coinbase 15m bars whose first receipt is <=t. Require the newest close no more than15m before receipt;
if a newer closed bar was already available, skip the obsolete backfill. Let `C0..C4` be positive USD closes
and `V0..V4` nonnegative base-asset volumes. Define `r=C4/C3-1`, `u=V4/median(V0..V3)`, `d=C4*V4` USD/bar,
and `b` the same-quarter BTC return from bars already received. `d` approximates dollar participation using
the close; it is not actual summed quote volume. A zero prior median abstains.

WATCH iff `u>=2`, `d>=5000`, `0<=r<=0.10`, `r>b`, and the pair was API-tradable/USD/non-stablecoin as of t,
with a usable Robinhood BBO received in `[t-120s,t]`. Price-only control is `r>=0.03` on this available venue
universe. Missing bar, gap, future-only BTC, invalid fields, no venue evidence or stale quote => ABSTAIN.
The five-bar context is75min; the denominator is four actual observations, not a statistical confidence claim.
Decision points are at most one per asset/quarter-hour. Coinbase participation is not Robinhood depth,
net buying, unique traders or all-market volume. Assets listed without usable bars remain in cohort coverage.

**Solana early reported flow.** Cohort identity is mint+pool+venue, first locally observed snapshot, including
untracked, rejected and deferred pools. Only the first24h after that observation can produce points; this is
local observation age, not token creation age. Use only `dexscreener.tokens_v1` snapshots so different sources'
aggregation windows do not masquerade as growth. Compare current m5 claims with the latest snapshot300–390s
earlier, require >=4 distinct timestamps and no inter-observation gap >120s. Identical endpoint payload
hashes abstain. This does not prove every underlying trade window is independent or unmanipulated.

Let `u=current_m5_volume/prior_m5_volume`, `n=current_buys+current_sells`, `q=buys/n`,
`r=current_price/prior_price-1`, and `l=current_liquidity/prior_liquidity`. Positive usable prices/liquidity,
valid activity and prior volume are required. WATCH iff `u>=2`, current volume >=$5000, `n>=50`, `q>=0.60`,
`0<=r<=0.10`, liquidity >=$40k, `l>=0.95`. Price-only control is `r>=0.03` with usable liquidity >=$40k.
One point per pool/5m bucket. These are **reported trade counts**, never unique-wallet counts. Source conflicts,
security failure or unavailable original-quantity exit never become permission to trade.

**Both tracks' outcome rules.** Horizons15m/1h/6h/24h; wait until horizon+90s, then use the first actual same
venue/source reference in `[target,target+90s]`. Never choose the best price in that interval or look ahead
outside it. Missing reference, missing endpoint, invalid price and zero liquidity are retained outcomes.
MFE/MAE are sampled reference-price diagnostics only when the entire observed path has <=120s gaps and
<=2000 rows. Otherwise leave them null. Even a complete sampled path does not reconstruct every tick.
No flat candle, synthetic liquidity, guessed fill, or zero-risk stop is substituted.

Quote marks require entry information received within10s before t and exit estimates within10s of the
endpoint, at the **original token quantity**, not a new same-dollar purchase. RH requires an entry estimate
within90–100% of the configured reference ticket ($45–$50 initially); totals include reported fees. Solana
uses the cached configured native security-probe amount (0.1SOL), separate from the dollar account. That
probe does not validate the new $50 account ticket's capacity. Spec hashes include the actual probe amount.
Absent exact-size information stays absent. Quotes are hypothetical; network costs, unobserved failed fills
and adverse movement remain unmodeled, so `net_return` and `net_expectancy` deliberately remain null.

Illustrative RH arithmetic, **not a trade result**: four prior volumes1000 units, current4000, closes100→104,
BTC same-quarter return0.5% => `u=4`, `d=$416000`, `r=4%`, excess3.5%: WATCH if venue/quote checks pass.
A $50 reference ticket is not a $500 trade. If a recorded BUY acquired0.45 tokens for$45.475 after fees and
a later exact0.45-token SELL estimated$67 net of its reported fee, the quote mark is `67/45.475-1≈47.33%`;
this is not realized return. If the only SELL is for0.30 tokens, quote coverage is missing even if the chart rose50%.

Illustrative Solana arithmetic: m5 volume$2000→$6000,60buys/20sells, price1→1.04, liquidity$50000→$49000:
ratio3,80trades, buy share75%, return4%, retention98% => WATCH if timing/validity checks pass. If liquidity
later vanishes while a stale chart prints2.00, record zero-liquidity/unavailable-exit failure, never a100% win.

Pseudocode: open source DB read-only → register spec → advance receipt/id cursor in bounded batches → retain
all observed identities → freeze as-of context and hash → record at most one bucket point → allocate label
work across all four horizons → append matured/missing outcomes → publish bounded descriptive report.
Each restart deduplicates by cohort+spec+bucket; separate venue cursors cannot mix their capital or evidence.

## Whole-trade proposal: specified, not switched on

WATCH alone is not a complete trading policy. For the next preregistered PAPER experiment, require a fresh
second check at the next closed15m bar on RH (or >=60s later on Solana), no security/identity/supply conflict,
all normal primary safety gates, a fresh intended-size BUY and exact acquired-quantity SELL route, and
estimated round-trip cost below the existing venue ceiling. Cancel on stale history, adverse source conflict,
lost liquidity/access or failure to recheck within30min. This confirmation's lost lead time must be measured.

Compare three **registered exit policies**, rather than assuming a trailing stop is optimal: a fixed6h
time exit; a cause-invalidation exit (participation returns below its prior median on two complete observations
or liquidity falls below90% of entry); and a frozen volatility trail using the maximum *executable liquidation
value* since entry minus2times a training-only, lagged realized-volatility dollar band, with a6h cap. At most
one position per asset; no averaging down, derivatives or pyramiding in this trial. Gap/stale exits retry under
existing guardian rules and remain exposed; no policy guarantees a requested stop price.

For a future trial compute `ticket <= min(0.10*eligible_equity, risk_budget/stressed_loss_fraction,
remaining_exposure, free_cash_after_fees, executable_capacity)`; initial stress-risk budget at most1% of
reference equity ($5 at$500), and total exposure/max-position limits retain their venue-specific settings.
`stressed_loss_fraction` must include exit failures, not just a stop distance; if its conservative upper bound
is unavailable, ABSTAIN. This formula is a **proposal**, not a silent replacement of existing PAPER sizing.
Future evidence-qualified increases may approach the existing15% cap only in separately validated sizing
experiments. Realized profit by itself is not qualification. LIVE remains outside this pass.

Trade-policy experiments need a proper joint portfolio simulator and enough prospective exact-size marks.
They are not implemented or claimed profitable by the observation lab. [Validation contract](VALIDATION.md)
defines purging/embargo, untouched evaluation, dependence-aware uncertainty, controls, ablations and promotion.

## Delivered minimum version, resources and next priorities

Implementation: `src/desk/research/{opportunities,rh_opportunities}.py`, research migrations0005–0006,
`src/desk/ops/opportunities.py`, CLI `desk opportunities run|report`, and a read-only RH Learning-page panel.
The panel reads an atomically replaced report file; it cannot start workers or make provider calls. Market
connections use SQLite read-only mode/query_only; the lab never opens a money ledger or loads credentials.
Only the authorized $500 change affects future PAPER size. Every historical position retains its contract.

Budget for the Ryzen7730U/16GiB/shared-GPU laptop: no GPU/model/server, zero incremental provider calls,
one optional minute-interval sidecar. Work defaults: <=2000source rows and200outcomes/pass; max10000/2000.
Labels share the work quota across horizons so shorter queues cannot starve24h follow-ups. Group display
uses latest10000outcomes with a truncation flag; full immutable records stay in SQLite. Query output/work
loops are bounded, but SQLite source scans/sorts and aggregate counts can grow with history: there is no
hard wall-clock guarantee. Time passes on the actual laptop and pause the sidecar if it competes with exits.

Offline measured workload:90synthetic pairs,8640bars,90research points; see [benchmark.json](benchmark.json).
This cloud process used about46MiB peak RSS and0.07s for ingest/label/report in that sample, **not a laptop
performance guarantee**. It measured no populated long-path outcome queue or multi-month database behavior.
Run `uv run python research/opportunities/benchmark.py` locally for a reproducible software/resource check.

Conservative planning bound, not measured market growth: <=8640RH points/day at90pairs; <=86400Solana
points/day at300fresh pools, both before eligibility/age reductions. At a provisional10KiB per point plus
four outcomes/indexes this is about85MiB/day RH and844MiB/day Solana. Actual payload sizes/duplicates vary.
At30days that is roughly2.5GiB and25GiB. Do not start unbounded Solana retention just because290GiB is free.
Reserve >=30GiB free, set a10GiB research review threshold, inspect `du -h ~/desk-data/db/research.db`, and
pause only the research sidecar for review/archiving. Existing market/raw retention is separate; never delete
ledger history. No automatic purge or storage alarm is implemented. On busy Solana feeds use400outcomes/pass
only after measuring CPU; a growing backlog is a diagnostic, not missing data to fill artificially.

Priorities after this version: (1) collect complete prospective outcomes/coverage; (2) quantify where filters,
latency or venue mismatch lose actionable lead; (3) improve archival receipt-time, identity, missing-market
and original-quantity execution coverage within the existing API budget; (4) build the matched-capital complete
policy simulator; (5) run a frozen forward evaluation; (6) only then consider a PAPER filter. Do not optimize
thresholds against the first big winner. Automatic entry/sizing influence is deliberately not implemented.

Unknown: current laptop config/branch and actual free-provider quotas; whether Coinbase omissions are venue
inactivity or provider loss; market-wide/dead-asset coverage; manipulation labels; reliable original-size
quotes and failed-exit distributions; migration/supply histories; causal diffusion; incremental net edge,
capacity and stable confidence calibration. No cloud research can retrieve the laptop's private database
without a configured connection. See [HANDOFF.md](HANDOFF.md) for exact operator commands and continuation.
