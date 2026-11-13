# The Learner

The Learner is KESTREL Stocks' machine-learning loop. Until now every trade came from a fixed menu: the 17 Plays
sections, the PAPER strategies, the Learning recipes and the lab's desk combinations, with the learning tuning inside
that menu. The Learner starts from the other end. It records **every** trade KESTREL looks at, not just the picks, with
what was known at that moment and what happened afterwards. A statistical model then learns from that record which
kinds of trade tend to work, and when, and which do not.

It is built in phases:

| Phase | What | Status |
| --- | --- | --- |
| 1 | The experience log: every candidate, its point-in-time features, its outcomes | built |
| 2 | An outcome model trained walk-forward, shown in **shadow** beside the rules | built |
| 3 | A Learner PAPER account that composes its own trades within risk limits, with directed exploration, against the rules and random | built |
| 4 | Written "do / don't" findings with evidence, regime awareness, promotion only after forward confirmation | built |

**What it never does.** It places no order (research and PAPER only: its own account trades simulated money). It does
not change a Plays ranking, another PAPER strategy, the Learning book or any calibration. No language model judges any trade: the model is ordinary
gradient-boosted trees (scikit-learn). It uses no paid API. Its rule sets are frozen by version and fingerprint
(tests/unit/stocks_spec_fingerprints.json), like every other rule set.

Code: `src/desk/stocks/learner/`. Storage: `stocks-research.db` (migrations `0019_learner.sql` and
`0020_learner_findings.sql`) and, for the account's decisions, `stocks-ledger-paper.db` (`0009_learner_account.sql`).
Every table is append-only.

## What it records

### Actions: trades as dials, not a menu (`learner/actions.py`)

A trade is described by four dials:

- **Structure**: long call, long put, call debit spread, put debit spread, cash-secured put, short put spread, short
  call spread, iron condor, long straddle, long strangle. These are the defined-risk structures Plays already offers;
  nothing sells an uncovered option.
- **Target |delta|** of the anchor leg (the bought leg of a long option or debit spread; the sold leg of a credit
  spread, cash-secured put or condor). For example 0.15, 0.30, 0.50 and 0.70 for a long call. A strike counts only
  when it is within 0.10 of the target.
- **Days to expiry**, as six buckets: about a week (5–10 days), two to three weeks (11–21), about a month (22–45), two to
  three months (46–90), four to six months (91–180) and six months or more (181–400). Inside a bucket the listed expiry
  nearest its middle is used.
- **Width** of a spread, or of a condor's wings: 2.5%, 5% or 10% of the stock price.

How a trade is left is the **exit-rule grid**: hold to expiry; take +25%, +50% or +100%; stop at −50%; take +50% or stop
at −50%; leave after 5, 10 or 21 sessions; take +50% or else leave after 21 sessions. The exit rule is not chosen at
entry. Every candidate is labelled with what *each* rule would have returned on the same path, so the model can learn
how to leave as well as what to enter.

The grid has 246 settings: version 1, fingerprint `actions.version_hash()`.

### Candidates (`learner/experience.py`)

Once, at the first build after a session closes (after Plays records that session), the Learner reads the same stored
chains Plays reads, priced the same way (`plays.table`, the chain's own smile where it fits), and records for every
stock with a priced chain:

- **the action grid**: every dial setting the chain can supply fairly (a positive debit below a spread's width, a
  positive credit below what a sold structure can lose, every leg quoted within a 35% spread), identical legs once;
- **every Plays pick** recorded for the session (every section and its Expiry filter cohorts), marked
  `plays:<section>#<rank>`;
- **every PAPER and Learning option position opened that session**, marked `paper:<account>` or
  `learning:<account>`.

Picks and positions are valued at the same session's quotes of their own contracts.

**Caps.** At most 60 grid candidates a stock and 6,000 a session: with more than 100 stocks, the per-stock cap falls to
6,000 ÷ stocks, never below 12. When the grid is larger than the cap, the kept candidates are a seeded sample that is
round-robin over the structures and random within each, so no kind of trade is dropped systematically. Picks and
positions are never capped.

**Exploration slice.** 30 grid candidates a session are marked `explore`, chosen at random and round-robin across the
structures (the seed is the session). In Phase 1 they trade nothing. They are the unbiased sample a Learner account
can trade later.

**What a row holds.** The exact legs (OCC symbol, bought or sold); the entry per share at the quotes (bought legs at
the ask, sold at the bid, as Plays); the money at risk with fees; the maximum gain; the expiry; and the features. Rows
are stored zlib-packed, one row per stock and session.

**Volume** (measured on the synthetic fixtures, about six stocks a session): about 315 candidates a session, about 88
bytes each compressed, plus about 58 bytes of labels each once all its checkpoints are written. On a real day with 60
optioned stocks and the full cap that is about 3,600 candidates and about 0.5 MB a session with labels (about
130 MB a year). The 6,000-a-session cap bounds it at about 0.9 MB a session. `kestrel learner` prints the real numbers.

A session the laptop missed is not recorded afterwards: its scores and quotes would no longer be the ones of that
evening.

### Features: only what was known (`learner/features.py`)

A candidate is decided on session S from **S's own option quotes**. Everything else is read **as it stood at the
previous session's close**:

- **Stock**: every desk's score and the committee's score and label band (from the daily score history); returns over
  5, 21, 63 and 126 sessions; distance from the 50- and 200-session averages and the 52-week high; realized volatility
  over 20 and 60 sessions; median dollar volume; IV30, skew (25-delta put minus call near 30 days) and term structure
  (90-day minus 30-day IV) from the decision chain; IV rank, how many days of history it rests on and how much of that
  came from Massive's closing trades; IV against realized volatility; days to the next results, announced (Finnhub
  dates first seen before S) or estimated (the last SEC 8-K results filing + 91 days), labelled; sector; the change in
  short interest and days to cover (only settlements at least 14 days old, FINRA's publication delay); open-market
  insider purchases minus sales over 90 days by filing date; price; whether it is an ETF.
- **Market**: SPY against its 200-session average, its 21-session return and realized volatility; VIX and its 5-day
  change; SPY's own term structure (the VIX term structure's stand-in); the high-yield spread and its change; the 10-year
  and 3-month Treasury yields.
- **Contract**: the dials; days to expiry; moneyness and delta of the anchor; position delta, theta and vega; anchor
  IV; widest leg spread; smallest open interest and volume; entry against the price; money at risk; maximum gain
  against it; break-evens against the price; the market's and KESTREL's (raw, uncalibrated) chances and the chance of a
  profit at expiry (`chain_odds`, `play_targets`); KESTREL's expected value against the money at risk; the expected
  move; whether the chances came from the chain's smile.

Every feature's source and availability is listed in `features.STOCK`, `MARKET` and `CONTRACT`. Every query goes
through `features.Reader` and carries `day < S`. A test (`test_no_feature_reads_anything_after_the_decision`) writes
wild values on and after S into every source the features read and proves that no feature changes.

A missing input is stored as missing, never as zero. Replayed days and ETFs have no desk scores; the model sees that
they are missing.

## What happened: labels (`learner/labels.py`)

For every candidate:

- **Checkpoints** at 1, 5, 10 and 21 sessions after the decision: the position valued at that session's closing quote
  as held, both at **liquidation** (bought legs at the bid, sold legs bought back at the ask) and at **mid**. A candidate
  that expired by then counts at its settlement.
- **Expiry**: intrinsic value on the official close of the settlement session (the session before, when the expiry is a
  market holiday).
- **Excursions**: the best and worst return over the checked closes and the settlement.
- **Every exit rule's return** on that same path.

**Returns** use Plays' own arithmetic (`play_marks.profit`): per contract after fees, on the money at risk. A bought
position's loss stops at −100% (it can always be left to expire); a sold one's loss stops at its maximum loss.

**Never estimated.** A closing quote is a stored quote observed no earlier than an hour before that session's close.
Forward, that means the chains collected that session. In replay, it means the contract's own daily bar (its closing
trade with the replay's stated spread). A leg without one makes that checkpoint **missing** for that candidate, for
good. A split between the decision and a checkpoint makes it missing too. Nothing is modelled. An exit rule reacts only
on closes that were checked; a time exit whose close is missing is missing.

**When.** Forward checkpoints are written three hours after their session's close. A settlement waits up to 45 days
for the official close, then is written missing. Replay checkpoints are written once their session's stock bars are
stored. Each label is written once, keyed by stock, session and checkpoint (`INSERT OR IGNORE`), so the daily job is
idempotent. Each label records the label rules' version.

## Replay examples (`learner/replay.py`)

Forward labels take weeks to months to arrive. To give the model years-scale examples now, the same candidates and
labels are built from the real past prices the replays already download:

- **Decision days**: the first session of every week whose replay inputs are complete.
- **Contracts**: each name's real monthly listing as of that day and its ladder of contracts' daily bars, plus the
  lotto names' weeklies when those are complete.
- **Prices**: the real closing trade with the replay's stated spread (6% of the price, at least 3 cents wide), volume
  standing in for open interest.

Features are read the same way and as they stood before the day. The replayed Plays picks of that day are added and
marked `replay_plays:<section>#<rank>`. There is no exploration slice in replay. Every replay row has source `replay`.
Replay rows are never mixed with forward rows without that flag, and replay never changes a forward grade. Replay chains
are sparse (about 14 strikes a name and month), so fewer dial settings find a strike than forward.

## How it learns (`learner/model.py`)

**The model.** scikit-learn's histogram gradient-boosted trees (80 trees of up to 31 leaves; the structure, expiry
bucket and sector as categories; missing inputs handled natively). For each candidate it predicts:

- the chance of a profit at 1, 5, 10 and 21 sessions and at expiry;
- the expected return on the money at risk if held to expiry, with its 10th, 50th and 90th percentiles;
- which exit rule of the grid would have done best.

The returns trained on are clipped to [−100%, +1000%] so a few huge long-option wins do not dominate. Every evaluation
uses the real, unclipped returns.

**Walk-forward, honestly.** Rows are ordered by decision session. The later half of the sessions is cut into three
consecutive test blocks. Each block is predicted by models trained only on:

- rows decided at least 5 sessions earlier (the **embargo**), and
- rows whose label was already known before the block starts (the **purge**: a 21-session or expiry label that resolves
  inside the test period is not trained on).

A test checks both rules on every block and every target.

**Replay as a feature, or weighted?** Both are evaluated on the same held-out rows. In one, the replay flag is a
feature. In the other, there is no flag and replay rows are down-weighted to the forward rows' total weight. The one
with the better held-out rank correlation is frozen.

**Frozen and registered.** The final models are trained on every labelled row, then pickled, compressed and hashed
(SHA-256). The file is kept under `<home>/learner-models/`. The registry (`stk_learn_models`, append-only) keeps the
hash, the data's sources, rows, sessions and date range, the feature list, the code version and the held-out results.
A model is loaded only when its file's hash matches the registry. Since model version 2 the market regime codes are
features, and each training also keeps its held-out rows, the labelled-row count per region of the action space (the
account's evidence) and writes the do / don't findings (below).

**Weekly.** The app's maintenance loop runs the training on the same cadence and under the same guards as the weekly
evidence evaluation (never beside a running evaluation or replay). It starts a niced, two-thread subprocess when a week
has passed since the last model, or when there is none yet and 40 stock-sessions have labels. After a failure it waits
six hours before trying again. `kestrel learner-train` trains now. **Nothing it produces is adopted automatically by
anything.**

**Compute budget** (`kestrel learner-bench`, `learner/bench.py`). Training reads at most 150,000 candidates: whole
stock-sessions, a seeded sample when there are more. The bench uses a synthetic year:

- 250 sessions, about 60 stocks a session at the 60-candidate cap (about 900,000 candidates), plus replay;
- every feature present, a fifth missing at random;
- planted signals and heavy-tailed returns.

Training on the capped 150,000 rows took **125 seconds** with a **peak of about 600 MB** (two threads, this
development machine). That covers the walk-forward evaluation, the final models and the importance. The real
training's time and peak memory are stored with each model.

## How it is evaluated (shown plainly)

Each model's held-out results, on later sessions it never trained on:

- **Rank correlation** between predicted and realised returns held to expiry: overall, and day by day with a 90%
  bootstrap range over independent days.
- **Calibration** of its profit chances at each checkpoint: reliability by tenths (predicted vs observed, with counts),
  the Brier score and the base rate.
- **Top tenth vs bottom tenth** of each day's candidates by predicted return, realised.
- **Against the Plays picks and against a random candidate on the same days**, as daily means with a 90% bootstrap
  range over independent days, and the number of days. "Random" is the average grid candidate that day. The
  exploration slice is shown too.
- The 10–90% range's actual coverage (it should hold about 80%).
- The best-exit choice's realised return against simply holding.
- **What it relies on**: permutation importance on held-out rows, in plain words. This is how much its ranking worsens
  when that input is shuffled.

Days are the honest sample size. Neighbouring decisions share their market, and long labels overlap, so even "days"
are not fully independent. The Learning report never calls a Learner finding stronger than **suggestive** while it is
shadow only.

## What you see

- **Plays → each play card: "Learner's view · shadow".** Its chance of a profit at 5 and 21 sessions and at expiry, its
  expected return held to expiry with the 10–90% range, and the exit rule it expects to do best. Labelled as a
  statistical model's estimate, not traded, not used by the ranking, not yet proven.
- **Plays → "Learner's own top ideas (shadow)".** The trades it would compose itself from the newest recorded close
  (at most two per stock), ranked by its expected return. Each shows its dials in words, its legs, entry and money at
  risk, and its predictions, with the model's held-out headline numbers.
- **Learning → the Learning report's "The Learner — its model, its PAPER account and what it learned" section.**
  What it has recorded, how much is labelled, the last training, its held-out results with days and ranges, its
  calibration and what it relies on; then what it learned to do and not to do (each finding in its own words, with its
  stage), its PAPER account against its random twin, the best and worst regime held out, and any version's forward
  verdict.
- **Learner (`#learner`, in the menu and the More sheet).** See "The Learner page" below.
- **`kestrel learner`.** The same status as JSON.

The worker writes the views to `stocks-learner-shadow.json`, at most every 30 minutes while the board changes. A page
load computes nothing. Each closed session's views are also appended to `stk_learn_shadow` once per model, so they can
be checked against what happened later (Phase 4's forward confirmation).

## What it can and cannot claim

It **can** say, with the sample sizes shown, how well its predictions ranked and calibrated on later data it never saw,
and how its top choices compared with the Plays picks and with random candidates on the same days.

It **cannot** claim that it would make money trading:

- Its held-out results are not trades: no fills, no position limits, no slippage beyond the quoted spread.
- Until there is enough forward data, they rest largely on replay rows. Replay uses closing trades and an assumed
  spread, and its chains are sparse.
- They are estimated from overlapping, correlated decisions.
- The synthetic numbers in tests and the bench check the pipeline and the budget only.

Promotion of anything the Learner suggests needs a fresh forward confirmation, under its own preregistered test
("Promotion only after forward confirmation" below). Its PAPER account trades simulated money: its fills are at real
quotes, but they are not real fills, and a model that ranks well can still lose once fees, spreads and the limits
bite.

## Checks (`kestrel check`)

- **learner.features**: every stored feature finite or missing; entry and money at risk positive; no candidate expires
  on or before its decision.
- **learner.labels**: every label return finite and at or above −100% of the money at risk; dated after its candidate's
  decision and written after it. The table also refuses a label dated on or before its decision.
- **learner.account**: every position of the Learner and its random twin has a bounded maximum loss, a defined-risk
  structure, an exit rule from the grid and the recorded decision whose order it is; it never risks more than was
  decided and never loses more than was at risk. Every decision is within 2% of equity, the session's 8% budget and
  the exploration share, and names a model in the registry (the random twin names none). A session with decisions has
  a "traded" record.
- **learner.findings**: every finding was found on its model's held-out days, on at least 15 days, cleared the
  correction and has the right sign; its stages are in order (confirmed, failed only after candidate; retired only
  after confirmed) and every later stage was judged on at least 10 days after the discovery window.
- The Learner's accounts are PAPER accounts, so **books.cash**, **books.positions** and **books.fills** cover them too
  (cash never negative, equity = cash + marks, fills only at stored quotes of that day).

## Regimes (`learner/regimes.py`)

A market regime is three plain labels, read from the market features each candidate already carries, all as they
stood at the previous session's close (SPY's term structure from the decision session's own chain):

| Label | Rule |
| --- | --- |
| Trend | **uptrend** when SPY is above its 200-session average, **downtrend** at or below |
| Volatility | **high** when the VIX is 30 or more, or 20 or more with SPY's term structure inverted (30-day IV above 90-day); **calm** under 20 with a normal term structure; **elevated** otherwise |
| Credit | **widening** when the high-yield spread rose 0.30 points or more over 21 observations, **steady** otherwise |

A missing input makes that label **unknown**, never a guess. The three codes are model features (model version 2), so
the model can learn that a trade works in one regime and not in another. Every model reports its held-out results per
regime (`by_regime`: rows, days, rank correlation, its top tenth against a random candidate), every finding carries
its discovery results per regime, and the Learner account reports its closed trades by the regime of their decision
session. Frozen as `stocks_learner_regimes` v1.

## The Learner account (`learner/policy.py`)

A PAPER account named **Learner**, with its own book in the PAPER ledger and **$50,000** of starting capital, like the
other option PAPER accounts. It reuses the PAPER engine (`paper/engine.py`, `paper/fills.py`) for fills, fees, marks,
expiries and equity rather than Learning's rules, so its marks, P/L and grade are computed by the same code as every
other PAPER account and `kestrel check` covers it.

### When it decides

At the closing build, right after the session's candidates are recorded (the `learner` job), and only before the next
session opens; a session decided later is not decided at all. The STOP file stops new entries, as for every PAPER
account.

### The gate: it trades only once a model has beaten random

The newest model of the current code must have, on its walk-forward held-out data:

- its top tenth of each day's candidates beating that day's average candidate (`deciles.top_minus_random`) on **at
  least 20 held-out days**, with the **90% bootstrap range's lower end above zero**, and
- a **positive held-out rank correlation**;

and its frozen file must match its hash. Otherwise the account **waits** and records why, in plain words, for every
session (`stk_learner_days`, shown on the page). Its random twin waits with it, so both are judged over the same
sessions.

### The decision rule: a contextual bandit

The candidates are that session's recorded grid candidates (every structure × |delta| × expiry × width the chains
supplied fairly), with their point-in-time features. For each the model gives the expected return held to expiry `mu`
and its 10th and 90th percentiles.

- **Uncertainty.** The outcome spread is `sigma = (q90 − q10) / 2.563` (at least 0.05). The evidence `n` is the number
  of labelled rows the model was trained on in the same region of the action space: structure, |delta| and expiry
  bucket (`model.evidence_cells`). The uncertainty of `mu` is `se = sigma / sqrt(n + 1)`.
- **Exploit** (85% of each session's new-risk budget): trades whose lower confidence bound `mu − 1·se` is above zero,
  best bound first: confident positive expectancy.
- **Explore** (a fixed **15%** of each session's new-risk budget, at most 1% of equity a trade, at most two trades a
  session): trades that are uncertain (lower bound at or below zero) but promising (upper bound `mu + 2·se` above
  zero), ranked by a **Thompson draw** `mu + se·z` (`z` standard normal, seeded by the session, account and candidate)
  and taken while the draw is above zero. Exploration is chosen first, so confident trades never crowd it out. When
  few trades are confident, the explored share of the risk actually taken is higher than 15%; the page shows the
  realised share.
- **Exit.** The exit rule the model predicts best for that trade (its best-exit classifier); hold to expiry when it
  has none.
- **Filter.** A confirmed "don't" finding removes every candidate it describes, for both accounts.

### Risk limits (fixed, of equity when decided)

| Limit | Value |
| --- | --- |
| At risk in one trade (fees and the fill limit included) | 2% (explored trades 1%) |
| New risk a session | 8% (of which 15% is exploration's) |
| At risk in all open and pending positions | 30% |
| On one stock / one sector | 6% / 12% |
| Open positions / new positions a session | 20 / 6 (one a stock a session) |
| Units an order | 10 |

Money at risk is the most a position can lose: what a bought structure costs, or a sold structure's width less its
credit, with the 10% fill limit (bought legs) or a 10% smaller credit (sold) allowed for. Only the ten defined-risk
structures of the action space are traded; nothing uncovered is ever sold. A sold structure is **fully
collateralised**: its maximum loss is held in cash while it is open, and pending orders reserve theirs. A cash-secured
put's maximum loss is its strike, so at 2% of $50,000 only puts on stocks under about $10 fit; that is intended.

### Execution

- **Entries** are orders filled by the PAPER engine at the **next session's** stored quotes: bought legs at the ask,
  sold legs at the bid, $0.65 a contract a leg, refused when a quote is stale (not from that session or not after the
  decision), crossed or empty, wider than 35% of its mid, when a bought leg's ask ran more than 10% (or 5 cents) past
  the decision's, or when a sold structure's credit fell below 90% of the decision's.
- **Exits** follow the trade's exit rule on that session's **real closing quotes** at liquidation value (bought legs at
  the bid, sold legs at the ask, after exit fees): a take-profit or a stop on the return on the money at risk, or the
  session count since the decision (decided the session before, so it fills on that session's quotes). A rule never
  reacts to a modelled price; an exit order fills only at real quotes (it waits up to three sessions, then the rule
  decides again); a sold structure is never bought back for more than holding it to settle could lose.
- **Expiry**: settled at intrinsic value on the official close, like every PAPER option position.

The labels' exit-rule returns read the close of the triggering session; the account fills the session after (except
time exits, decided a session early). The account's results are therefore a little different from the labels', by
design: they are what an account could have done.

### Its baselines and grade

- **Random twin** (`learner_random`): the same candidates, sessions and limits; each trade a uniformly random
  candidate and a uniformly random exit rule (seeded by the session), sized at 2%, until the session's budget is used.
  It is the account's PAPER baseline: the Learner is graded against it (`paper.report`, Holm across every graded
  strategy) and against SPY's total return, like every PAPER strategy (63 sessions and 20 closed trades before any
  grade).
- **The rule-based PAPER option strategies**: their average equity is drawn beside it, and realised returns by entry
  day are compared.
- **Realised comparisons** use realised returns on the money at risk at real fills only, averaged by entry day with a
  90% bootstrap range; a position still open, or closed at a modelled price, is left out. Equity curves use the
  PAPER engine's marks (mids; a contract missing from the day's chain is marked by Black-Scholes, as for every PAPER
  account), so they are never used to promote anything.

### What it records

Every chosen trade (`stk_learner_decisions`, in the PAPER ledger, in the same transaction as its order): the model's
hash, its expected return and range, `sigma`, the evidence count, `se`, both bounds, the Thompson draw, explore or
exploit, the exit rule and the model's confidence in it, the units and money at risk, equity, the regime, the five
best alternatives weighed, and the reason in words. Every session's outcome (`stk_learner_days`): traded, waited or
found nothing, and why, with the budgets, the candidates eligible and what the limits skipped.

## Do / don't findings (`learner/findings.py`)

Written by the system, from **held-out data only**: each model's walk-forward held-out rows (predicted by models that
never trained on them, with their realised returns held to expiry), grid candidates only. They are kept with the model
(`stk_learn_heldout`, about 1.6 MB a model at the 150,000-row cap).

1. **Discovery** uses the earlier two thirds of the held-out days. For each structure, two depth-2 regression trees
   (leaves of at least 40 rows and 5%) on readable inputs (expiry, |delta|, days to expiry, moneyness, IV rank, IV
   against realized volatility, momentum and committee scores, 1- and 3-month return, price against its 200-day
   average, days to results, skew, spread, and the three regime labels): one on the realised returns, one on the
   model's held-out predictions. Every leaf is a rule, thresholds rounded to readable steps. Each structure, and each
   structure × expiry bucket, is a rule too.
2. **Testing**, by day (each day's mean is one observation): a **do** needs a positive mean with its 90% bootstrap
   range above zero; a **don't** needs a negative mean **and** a mean below the same day's average candidate, with that
   range below zero (worse than random, not only a losing trade). At least 15 days and 60 rows. The one-sided t-test
   p-values of every rule tried in the run are corrected by **Benjamini-Hochberg at 10%**. Overlapping rules (80% the
   same rows) keep the stronger; at most 12 of each kind. Passing makes a **candidate**.
3. **Confirmation** on the untouched later third (after a 5-session embargo): at least 10 days in the same direction,
   one-sided p below 5%, range excluding zero → **confirmed**; otherwise **failed** (kept). A candidate without enough
   later days is re-checked at the next training on the newer held-out days.
4. **Retirement**: each training re-checks confirmed findings on the held-out days after their confirmation; a mean
   that has turned the other way over at least 10 days **retires** it (kept).

Each finding is written in plain words with its numbers, for example "Do: Long calls with IV rank at most 20 and price
against its 200-day average at most −15%: +26% average held to expiry (90% range +17% to +35%), 75 candidates over 40
days, held-out" (a synthetic example). Every stage is a new row (`stk_learn_findings`, `stk_learn_finding_status`). A
confirmed **don't** is a hard filter for the Learner account; a **do** is information only. Frozen as
`stocks_learner_findings` v1.

## Promotion only after forward confirmation (`learner/promotion.py`)

A **Learner version** is the model code (the features, targets and training procedure every weekly refit shares) with
the policy. It is **provisional** from the session it first trades. Its forward test is fixed in advance: its first
**20 entry days** (decision sessions on which the Learner opened at least one position under that version), judged once
every position decided on them has closed:

- **against Random**: each day's mean realised return of the Learner's trades minus its random twin's, paired by day,
  90% bootstrap range over days;
- **against the rule-based option strategies**: the Learner's daily mean minus theirs over the same calendar window,
  90% range resampling each side's days; at least 5 rule entry days are needed, otherwise it stays provisional and says
  so.

Both lower ends above zero: **confirmed**. Otherwise **failed**, kept and shown; a new version starts its own test.
Realised returns at real fills only. Stages are appended to `stk_learn_promotions`. Frozen as
`stocks_learner_promotion` v1. Nothing is adopted anywhere by a confirmation: it is a label of evidence, in the style
of the Learning promotions.

## The Learner page (`#learner`; `learner/account.py`)

- the account's status: trading or waiting, and the gate's reason with its numbers; the rule and the limits in words;
- the Learner, its random twin, SPY (total return) and the rule-based option strategies' average on one chart, with a
  named legend; its grade (the PAPER grade); the realised comparison by entry day with ranges; the exploration share;
- open and closed trades, each with its dials, legs, entry, money at risk, result, and its decision: the model's
  view, its uncertainty and evidence, explore or exploit, the chosen exit rule and the alternatives weighed;
- what it learned to do and not to do, with each finding's stage, its regimes and whether it filters trades;
- by market regime: the model's held-out results and the account's closed trades;
- model versions: each model's held-out headline, whether it passed the gate, its forward trades, and each version's
  promotion stage and forward test;
- recent sessions: traded, waited or nothing, and why.

One column on a phone. The worker saves it to `stocks-learner-account.json` after each Learner pass and PAPER step
(when anything it shows changed); `GET /api/stocks/learner/account` serves it; a page load computes nothing.

## Compute (measured, synthetic)

On this development machine, two threads, synthetic data only:

- **Training at the cap** (150,000 candidates, 250 sessions): the walk-forward evaluation took 82 seconds; storing the
  75,100 held-out rows 0.6 seconds (1.6 MB); looking for findings 4.6 seconds (149 rules tested); peak memory about
  550 MB. Training now also writes the findings, so a weekly training takes a few seconds longer than before.
- **Deciding a session**: scoring 6,000 candidates (a full session at the cap) took 0.28 seconds; a whole decision on
  the six-stock synthetic market (about 300 candidates) took 0.05–0.2 seconds.
- **The Learner page's data**: well under a second on the synthetic month.

## Building on it

- **Tables**: `stk_learn_candidates`, `stk_learn_labels`, `stk_learn_days`, `stk_learn_models`, `stk_learn_shadow`
  (phases 1–2); `stk_learn_heldout`, `stk_learn_findings`, `stk_learn_finding_status`, `stk_learn_promotions`
  (research); `stk_learner_days`, `stk_learner_decisions` (PAPER ledger), with the account's positions, orders and
  equity in the usual PAPER tables under the accounts `learner` and `learner_random`.
- **Functions**: `policy.gate`, `policy.score`, `policy.choose`, `policy.decide`; `findings.mine`, `findings.matches`,
  `findings.current`; `promotion.review`, `promotion.judge`; `account.build`; `regimes.of`.
- **Frozen rule sets**: `stocks_learner_regimes`, `stocks_learner_findings`, `stocks_learner_policy`,
  `stocks_learner_promotion`, `stocks_learner_model` v2, and the two PAPER strategies `stocks_paper_learner` and
  `stocks_paper_learner_random` (their parameters include the policy's fingerprint, so a policy change needs new
  versions of both).
