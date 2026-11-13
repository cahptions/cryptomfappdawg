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
| 3 | A Learner PAPER account that composes its own trades within risk limits, with directed exploration, against the rules and random | next |
| 4 | Written "do / don't" findings with evidence, regime awareness, promotion only after forward confirmation | next |

**What it never does.** It places no order (research and PAPER only). It does not change a Plays ranking, a PAPER
strategy, the Learning book or any calibration. No language model judges any trade: the model is ordinary
gradient-boosted trees (scikit-learn). It uses no paid API. Its rule sets are frozen by version and fingerprint
(tests/unit/stocks_spec_fingerprints.json), like every other rule set.

Code: `src/desk/stocks/learner/`. Storage: `stocks-research.db`, migration `0019_learner.sql`, every table
append-only.

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
A model is loaded only when its file's hash matches the registry.

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
- **Learning → the Learning report's "The Learner — a shadow model, not traded" section.** What it has recorded, how
  much is labelled, the last training, its held-out results with days and ranges, its calibration and what it relies
  on.
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
(Phase 4).

## Checks (`kestrel check`)

- **learner.features**: every stored feature finite or missing; entry and money at risk positive; no candidate expires
  on or before its decision.
- **learner.labels**: every label return finite and at or above −100% of the money at risk; dated after its candidate's
  decision and written after it. The table also refuses a label dated on or before its decision.

## For Phase 3

What exists to build on:

- **Tables**:
  - `stk_learn_candidates`: per stock and session; `experience.unpack` gives the stock and market features and every
    row's key, origin, `explore`, action, entry, money at risk, maximum gain, expiry, legs and contract features.
  - `stk_learn_labels`: `labels.unpack_labels`; checkpoint rows are `[index, status, mid, liquidation]`; expiry rows are
    `[index, settled, best, worst, path sessions, checked, [each exit rule]]`.
  - `stk_learn_days`, `stk_learn_models` and `stk_learn_shadow`.
- **Functions**:
  - `actions.grid()`, `actions.legs_for(table, action)` and `experience.priced(...)` compose and price any action on a
    live chain;
  - `features.Reader` with `stock_features`, `market_features` and `contract_features` gives point-in-time features;
  - `model.latest` with `model.load_models`, `model.row_vector` and `model.predict` give predictions for any candidate;
  - `labels.exit_returns` gives the exit-rule arithmetic;
  - `experience.stratified` gives the stratified sampler the exploration slice uses.
- **The exploration slice** (`explore = 1`) is the unbiased sample a bandit account can trade and learn from.
