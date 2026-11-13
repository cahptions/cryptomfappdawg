# PAPER Learning workspace

Open **Learning** in Stocks. It runs automatically with the dashboard during regular market hours (9:30 to the close the
desk calendar gives for that day, so it stops early on a half day) when the selected options provider is connected.
Outside them it still settles expired positions and writes check-ins. **Pause Learning**, the global PAPER pause, and
STOP all suspend its funding and trading.
An explicit pause persists across dashboard restarts. No brokerage orders or real transfers are possible here.

## Capital and evidence

The independent `stocks-learning-paper.db` contains four books:

- **Research account:** started with a $500,000 reference deposit.
  - **Until 2026-10-30:** it received automatic virtual deposits whenever fills needed more cash.
  - **The cap:** at the user's request, it was capped once, at what it held when the update first ran. The page names it by
    that amount, such as "$2,317,215 research". The cap is stored as `research_cap` and never reset.
  - **Since then:** it trades only its own cash, with orders sized to fit, like the other books. Earlier deposits stay in
    its funding ledger. There are no other ceilings on trades, positions or contract quantities.
- **$500,000 reference:** receives the same order intents but buys only what its remaining cash can afford, including impact
  and fees. It receives no top-ups and cannot restrict research. Its result measures the cost of finite buying power.
- **$1,000 account** and **$5,000 account:** choose their own affordable contracts using research available before each
  decision. They keep independent cash, orders, positions, costs and results, with no automatic top-ups or resets after
  losses. They can select different strikes, expiries or quantities from research and from one another.

Equity is cash plus marked positions. **Trading P/L = equity minus all contributed capital**, with realized/unrealized P/L,
fees, marked capital employed, contributions and funding-excluded dollar drawdown reported separately. Additional deposits
cannot cure a trading loss. Financial numbers must remain finite and representable in the integer-microdollar ledger;
this is a storage constraint, not an account allocation rule.

No destructive reset or failed-experiment deletion is provided. Prior source observations, specifications, orders, partial
fills, cash movements, closed trades, outcomes and check-ins remain stored. A changed sizing policy gets a new hashed
specification, with a parent hypothesis and change description. Ordinary Stocks strategy accounts and historical backtests
retain their own ledgers and behavior.

## Small-account tests

The Learning page always shows both small accounts together: equity, trading P/L, return on starting capital, cash,
trade counts and policy status. Select an account to inspect its own chart, open/closed trades, orders, fills and original
decision evidence. Accounts start forward when this update first runs; earlier research returns are not scaled into
invented small-account histories. A single starting observation is explicitly identified rather than drawn as a history.

The transfer policy groups prior uncapped outcomes into recipe families: structure, expiry bucket (lifecycle recipes),
holding time and entry spacing. A 30-day hold of 1-month options and a 30-day hold of 3-month options are different
families, so the expiry window a small account buys from always belongs to the evidence that chose it. Repeated fills
and sizing variants are averaged within underlying/day blocks, then their mean net return is shrunk toward zero. Only outcomes that
matured strictly before a decision, within the previous 90 days, can enter that decision. The structure of a **confirmed
finding** (below: corrected test, then untouched confirmation days) takes priority among positive families; a
provisional promotion, and any promotion from before the 2026-11-13 rules, does not. Otherwise selections are explicitly
**provisional**, not validated predictions. Small-account outcomes do not train the uncapped model or inflate the transfer policy's research sample.
If all available research families have nonpositive estimates, the small accounts hold cash while uncapped experiments
continue. Before any research outcomes mature, a visibly labeled fixed starter long-call policy provides a baseline.

**When they sell.** A small account sells a lifecycle recipe's position at the recipe's **best holding time so far**:
the checkpoint with the highest average return among those with at least 10 research positions valued in the previous
90 days and strictly before the decision (ties go to the shorter hold), still selling early when expiry gets close. Until
a checkpoint qualifies it holds for the recipe's full horizon. The chosen holding time, its average, position count and
basis are frozen in the decision, the order's plan (`hold_minutes`, with the recipe's own as `recipe_hold_minutes`) and
the policy hash, and the account panel shows "Sells after N days". Checkpoints compare the positions that lived that
long, so a longer one can rest on fewer, surviving positions.

Sizing targets 25% of current equity, including modeled impact and entry fees. Whole option contracts are required: if no
eligible contract fits the target, one may exceed 25% while remaining within available cash. This concentration is disclosed
in the decision and page. Contracts that cannot fit available cash are skipped. The engine examines alternative eligible
strikes and expirations, checks freshness/liquidity, and fills only on a later actual quote; a price jump can reduce or
cancel the order. A selection records its research cutoff, contributing experiments, block/day counts, model version,
policy hash, sizing inputs and reason. Original evidence stays fixed as research subsequently changes.

The small accounts share quote collection and estimated execution rules, but maintain independent hypothetical liquidity
usage and results. Their pending entries and marks receive scheduling time even with a large uncapped experiment queue.
Pause Learning, global PAPER pause and STOP also suspend these accounts. These are forward PAPER tests of transfer to
smaller capital, not estimates of guaranteed potential profit.

## Experiments and execution

### Lifecycle recipes (since 2026-10-31)

A 30-minute or 2-hour hold measures intraday noise; it cannot say whether a 3-month option or a LEAPS pays, or whether
holding longer does. So the registry trades **every expiry** and values each position **as it ages**:

| Bucket | Days to expiry | Held up to | Sold early when | Valued after (days) |
|---|---|---|---|---|
| Weekly | 5–12 | 7 days | 1 day remains | 1, 7 |
| About 1 month | 21–45 | 30 days | 3 days remain | 1, 7, 14, 30 |
| About 3 months | 60–120 | 90 days | 14 days remain | 1, 7, 14, 30, 60, 90 |
| About 6 months | 150–240 | 120 days | 30 days remain | 1, 7, 14, 30, 60, 90, 120 |
| LEAPS (9+ months) | 270–800 | 180 days | 60 days remain | 1, 7, 14, 30, 60, 90, 120, 180 |

- **15 recipes:** long calls, long puts and long straddles at the 0.5-delta strike in each bucket, $5,000 an entry
  (at least one contract), at the expiry nearest the middle of the bucket's window.
- **One purchase, many answers:** a position is bought once, and at each checkpoint the first later fresh quote records
  what selling it then would have returned (bid less impact and exit fees, never below zero), in
  `stk_learning_checkpoints` (append-only). The position itself is sold at its horizon, or earlier when expiry gets
  close, so no LEAPS result waits for expiry. A contract whose bid would bring no more than its exit fees is not sold
  (that would lose more than was paid): it is written off at zero, no fee, exactly -100% (from 2026-11-13; earlier
  outcomes are kept as recorded).
  **Does holding longer pay?** is then read from the same entries: Learning shows each recipe's average return, win rate
  and count at every checkpoint, and names a best holding time once a checkpoint has 10 positions. The small accounts
  sell at that holding time (below).
- **Pacing:** a recipe re-enters the same stock at most once a week and at most twice a day across stocks, so evidence
  comes from many days rather than one burst; and each recipe holds at most an equal share (1/15) of the research
  account, so long-dated recipes cannot crowd out the rest.
- **Collection** also keeps the expiry nearest 6 months (beside the weekly, ~30/60/90-day and ~1-year expiries), and
  Learning's own collector quotes every contract its positions and pending orders hold, every minute (below).
- **The minute-hold seeds are retired**, not deleted: on the switch they stop opening positions, their open positions
  still exit under their own rules, and their outcomes stay in the record. The $1,000/$5,000 accounts copy only active
  recipes (retired results are history); until lifecycle results mature they use the 1-month call starter. Lifecycle
  recipes are compared at a fixed size, so no size-doubling follow-up is created from them.

**Registering a recipe.** The Experiments tab's form registers another lifecycle recipe: a structure, an expiry bucket
and how long to hold it, from 1 day up to the bucket's longest hold (its latest expiry less the days left when it is
sold: 11, 42, 106, 210 or 740 days). It buys and is paced like the seeds ($5,000 an entry at the 0.5-delta strike, at
most once a week per stock and twice a day), is valued at every checkpoint up to its holding time (with 270- and
365-day checkpoints for long LEAPS holds), and runs beside the seeds with an equal share of the research account. The
same choices always make the same recipe, so choosing a seed's own holding time adds nothing. The form no longer
registers minute-long holds: minute-hold experiments registered earlier keep running under their own rules, and a
promotion's doubled-size follow-up for one of them is unchanged. Positions may overlap.

For every decision, the ledger freezes its hypothesis, policy hash, underlying price, original timestamped chain and
provisional expected return. Entry waits for a **strictly later** quote. Quotes must be at most 120 seconds old, not from
the future, and have a usable two-sided spread. Long entries pay ask plus adverse square-root impact; exits receive bid
minus impact. Fees are $0.65 per contract per side. Long premium plus fees is fully cash-collateralized; virtual funding
can provide that cash. Naked shorts, assignment/exercise financing and short-leg structures are not supported by this lab.

**Capacity** per contract quote timestamp is the **size displayed at the side traded**: the ask size when buying, the
bid size when selling, as Schwab reports it on chain rows and per-contract quotes. It is capped at 0.1% of open interest
(minimum one), and that rule alone applies when a quote carries no size (another provider, or chain rows saved before
sizes were kept). Usage is shared across all orders in each independent hypothetical book. Repeated snapshots with the
same contract timestamp do not replenish capacity or repeatedly count cumulative daily volume. Orders can partially
fill, and the remainder stays unfilled until a later quote or its 90-minute timeout; an exit waits until one quote shows
enough size at the bid. The GUI shows requested, filled and unfilled quantities, and each fill and exit records the
size it saw. The genuine limit is that there is **no depth beyond the top of the book**: the size behind the best bid
and ask is unknown, so a fill never takes more than was displayed. Unconstrained requested exposure is not represented
as executable profit.

The holding horizon is elapsed clock time. Positions exit on the first later usable quote with sufficient modeled
capacity, so actual holding can exceed the target. Missing, stale or too-wide quotes do not generate imaginary exits.

**Expiry settlement.** A position still held when its options expire (its quotes were missing, too wide or too thin to
sell before then) settles at **intrinsic value on the official close** of its expiry day, the close stored in the
market database's daily bars, exactly as the ordinary PAPER books settle expiring options. This is a real outcome, not
a stand-in: an expiring contract's value is fixed by that close. Learning holds only long calls, puts and straddles, so
nothing is ever assigned. An in-the-money leg pays the $0.65 exit fee (it is exercised and sold at the close); a
worthless leg pays nothing, and so does a position in the money by less than its fee (it lapses: -100%, never below). Settlement runs on every Learning cycle, including evenings and weekends, as soon as the
close is stored; the cash entry is `EXPIRE`, the trade's exit reason names the close, and the research outcome is
recorded with basis `settled at intrinsic` (sold trades: `sold at a quote`). It matures when it is settled, never
back-dated to the expiry, so no earlier decision can see it. Until the close arrives the position is shown as waiting
for it, with its last quoted value. One case stays open on purpose: if the stock split while the position was held, the
contract was adjusted and the unadjusted strike would settle the wrong amount. The app does not claim these modeled
results prove real execution capacity.

Provider polling and machine scheduling are separate from financial limits. Provider collection is paced to a minute
and shares the desk's provider rate limiter. Each minute it first quotes **every open position's and pending order's
contracts** through Schwab's per-contract quotes, 100 contracts a request and up to 10 requests (1,000 contracts) a
minute; a larger book rotates through its contracts over a few minutes. Those quotes, with their quote times, are saved
as an observation and feed the next evaluations, so a fill, a checkpoint or an exit no longer waits for a chain pull to
reach its stock and expiry. Then it rotates two underlyings and two expirations per pass for **new entries**, plus any
held expiry the per-contract quotes did not reach (for example after a failed request). Per-contract quotes are used
only while Schwab is the selected options provider, so one Learning book never mixes providers; with another provider
the rotation serves held positions as before. Saved observations are evaluated on a 15-second schedule with rotating
batches for orders, positions and proposals; a contract quoted both ways uses the newer quote. These batches advance
their cursors rather than permanently discarding excess experiments. Collection has its own source-health job and never
postpones the ordinary options collector. Closed markets and missing connections produce readable statuses and retry later.

## Learning and check-ins

This is an initial empirical research engine, not a proven prediction system. Registered hypotheses run before there is
enough evidence for a grade. Only later closed outcomes can update the provisional, shrunk block-mean forecast. Multiple
fills are grouped by underlying/entry day rather than counted as independent proof. Dollars of profit and number of
contracts do not increase the sample's independent evidence count.

Chronological comparisons select among structure families on the earlier half of entry dates and evaluate on later dates.
Training labels must mature before test entries. The comparison reports its uninvested-cash baseline and separate
finite-capital book; all challengers and losses remain.

**Promotion (rules of 2026-11-13, `src/desk/stocks/learning_promotion.py`).** The earlier rule (at least 20 new test days
and an approximate mean-minus-three-standard-errors hurdle over day means, with no correction for how many recipes were
compared and no confirmation) was exploratory. A recipe now becomes a **finding** only in three steps:

1. **Frozen manifest.** When a recipe is registered, its manifest (exact configuration, hypothesis, metric, sample, test,
   correction and confirmation rule) is frozen with a SHA-256 hash in `stk_learning_manifests` (append-only, ledger
   migration `0008`). Recipes that existed before the rules were frozen when the rules arrived and are marked
   `backfilled`.
2. **Provisional, after correcting for the number of recipes.** The sample is the recipe's entry-day means (each day's
   mean of its underlying/day block means) on held-out entry days (the later half) after any failed confirmation; it
   needs at least 20 days. Its one-sided t-test p-value (mean above zero) must clear **Holm's step-down correction across
   every active recipe** at family alpha 5% (recipes without 20 days, or already confirming, count as p = 1: with the 15
   seeds the best recipe must reach p < 0.33%). Passing records a **provisional** promotion with the confirmation rule
   fixed in advance.
3. **Confirmed on untouched days.** The confirmation sample is the recipe's **next 20 entry days after the promotion day**,
   decided once every position entered on them has finished. None of those days took part in the promotion. Their
   day means must again be above zero at one-sided 5% (one pre-specified test). Pass: **confirmed** (the only stage
   called a finding, the only one that gives its structure priority in the small accounts, and the only one that
   registers a doubled-budget follow-up for a non-lifecycle recipe). Fail: **failed confirmation**, kept and shown; the
   recipe can be promoted again only on entry days after that window.

Every decision is appended to `stk_learning_promotions`. Promotions made before these rules stay in the record,
relabelled **provisional (before 2026-11-13 rules)**, and no longer give their structure priority. The Learning page's
**Promotions** table and the Learning report show each stage with its evidence. Re-testing a growing sample at each
check-in is a form of repeated looking, which is why nothing short of confirmation is called a finding; overlapping
exposures and top-of-book-only size still limit confidence, and a confirmed finding is not a guarantee of profit. The
small accounts keep using only research outcomes matured strictly before each of their decisions.

Formal comparison starts after at least **4 entry days with completed outcomes and 12 distinct underlying/day blocks**,
with eligible earlier training labels and later held-out entries. These are evidence requirements, not spending or trade
limits. Many fills in one stock on one day can produce dozens of outcomes but only one block. A version-0 formal model
does not mean those outcomes or provisional per-experiment estimates are absent.

**What the system has learned** shows the actual completed research outcome count, block/day counts and minimum progress,
the exact remaining blockers, and the last completed outcome, usable observation and check-in times. Expand **Early
experiment results** for provisional observed returns by recipe family (structure, expiry, holding time, entry spacing). Repeated fills and
sizing variants are averaged within each underlying/day; both stronger and weaker families are retained in the summary,
and every original experiment remains available. These descriptive averages are separate from held-out model results.
The count is always based on uncapped research, even while inspecting a reference or small account. Closed research trades
missing an outcome record are explicitly reported rather than silently treated as either trained evidence or invented labels.

Saved observations are processed **before** provider requests. A slow or failed request cannot postpone that cycle's saved-
data processing, and already-completed outcomes can receive a check-in after market hours without opening new trades.
Check-ins refresh once after this reporting update even if the outcome count has not changed. GET reports are read-only;
they do not create trades, labels or a trained model. Pause/STOP continue to control the Learning worker.

The page shows model version, training/test coverage, changes, open/closed trades and daily/Friday weekly check-ins, with
Markdown/JSON downloads. Options shows a separate learning-findings panel and links to the supporting workspace. Learning
does not blend social mentions or unusual-volume rankings into its evidence count. There is no supported finding until
a recipe passes its confirmation days.

## Learning report

The top of the Learning page is the **Learning report**: one readable page, computed from this computer's own
databases, that says what every learning loop knows (`src/desk/stocks/learning_report.py`,
`GET /api/stocks/learning/report`). Its headline (two to four sentences) is always visible; the sections fold:

| Section | What it reads |
|---|---|
| What it has learned | Calibration now in use ("when KESTREL said 60–70% for Trend calls, 41% finished in the money … it now shows those as about 45%"), suggested exit rules, inputs that predict a week's return, challengers that won or were rejected, best holding times, the lab's promotions, options rich or cheap |
| How it behaves | What it picks (sections, calls/puts/straddles, buying or selling, days to expiry, price, sectors, stocks) over the last 90 days, its odds against the market's own (more bullish? more confident?), Learning's mix, invested share and turnover, research against the $500,000 reference, concentration of P/L |
| What it's getting wrong | Over/under-confidence by chance with the size of the gap, sections trailing their random twins or failing, losing recipes and Expiry-filter cohorts, small accounts losing, the biggest high-confidence misses, PAPER strategies behind or backwards, and data problems (estimated/modelled marks, failing jobs, Schwab history refused, quarantined plays) |
| Achievements | Proven sections, promotions, calibration improving the Brier score against the uncalibrated model on the same plays, beating the market's odds, the biggest lotto multiple and winners, the best Learning and PAPER trades, milestones |
| Surprises | E.g. a section whose odds are right but which loses money, a recipe that pays at one holding time only, an input that points the wrong way, a profit resting on a few trades |
| Coming up | The nearest milestones with progress and, where there is a pace, an estimated date ("take-profit calibration for Bearish puts: 22 of 30 resolved, about a week"); a loop with no data says what it is waiting for |
| Changed since last week | Counts, scores and grades against the previous weekly snapshot, and findings that are new |
| Replays on past data | Historical replays, preregistered option research, lottos on real past prices and the Evidence page's rebuilt history and proposed weights, always labelled and never mixed with forward results |

**Honesty rules.** Every finding shows its evidence, the number of items **and** of separate sessions or days, and a
strength: **early** (below that loop's own threshold, such as 30 resolved plays from 10 sessions for calibration or 60 settled plays
over 20 sessions for a section verdict, or seen on fewer than 10 separate days whatever the count: 30 plays from 2
sessions are early), **suggestive**, or **tested**, used only where that loop's own significance test or grade
already says so (a proven or failing section, a suggested exit rule, a promotion, a working or backwards grade, a
replay's bootstrap). Calibration reads graded results only, so estimated and modelled marks are never outcomes;
recently graded plays use the current probability model only, like the Plays page. Proposed committee weights are
shown as proposed unless you adopted them.

**Read-only.** Building the report opens every database read-only and reads the saved views; it never trades,
grades, adopts weights, calls a provider or an LLM, and no key or credential appears in it (failing jobs are named,
their error texts are not). Aggregates run in SQLite, row reads are limited, and the two loops that read every graded
chance and recent mark are remembered until those tables change. On a synthetic two-year desk (152,700 plays,
576,000 graded chances, 1.56 million marks, 30,000 Learning positions) a first build took about 5 seconds and later
ones about 0.7 seconds.

**Weekly snapshot.** After Friday's close (16:30 New York, or over the weekend if the worker was off then), the
collection worker appends one snapshot per week to `stk_learning_reports` (research migration
`0015_learning_reports.sql`, append-only). "Changed since last week" compares with the newest snapshot from an
earlier week. A page load never writes one. The Check-ins tab lists the weekly reports; the Markdown check-in
download includes the report, and **Download learning report (Markdown)** sits beside it (also at the foot of the
report).

## Verification

Offline tests cover funding after losses, exclusion of deposits from P/L/drawdown, later-quote execution, shared
capacity at the displayed bid/ask size, partial fills, expiry settlement at intrinsic on the official close, per-contract
quotes of held legs, immutable history, temporal label separation, losing held-out results, policy lineage, independent
collection, recipe registration, CSRF and persisted pauses. Small-account tests also cover different affordable
strikes, later-quote price jumps, no overdrafts/top-ups, retained losses on restart, prior-only research evidence
(including the holding time), and exclusion of repeated or finite account outcomes from independent research counts.
The `learning-workflows` and `learning-retail` browser fixtures exercise engine-generated synthetic trades, funding,
comparisons, account selection, trade details, registering a lifecycle recipe and downloads at desktop and phone
widths; the `learning-report` fixture (Plays history, the Learning book and an earlier weekly snapshot) exercises the
Learning report's sections, strength chips, links and downloads at 1366 and 375 pixels.
These checks validate implementation; they are not live provider or profitability evidence.
