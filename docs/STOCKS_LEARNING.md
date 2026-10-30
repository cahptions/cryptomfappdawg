# PAPER Learning workspace

Open **Learning** in Stocks. It runs automatically with the dashboard during regular market hours when the selected
options provider is connected. **Pause Learning**, the global PAPER pause, and STOP all suspend its funding and trading.
An explicit pause persists across dashboard restarts. No brokerage orders or real transfers are possible here.

## Capital and evidence

The independent `stocks-learning-paper.db` contains four books:

- **Uncapped research:** a $500,000 starting reference deposit. The engine automatically records additional virtual deposits
  when fills need more cash. There are no policy ceilings on aggregate spending, trades, positions or contract quantities.
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

The transfer policy groups prior uncapped outcomes by structure, holding time and entry spacing. Repeated fills and sizing
variants are averaged within underlying/day blocks, then their mean net return is shrunk toward zero. Only outcomes that
matured strictly before a decision, within the previous 90 days, can enter that decision. A previously recorded promoted
structure takes priority among positive families; otherwise selections are explicitly **provisional**, not validated
predictions. Small-account outcomes do not train the uncapped model or inflate the transfer policy's research sample.
If all available research families have nonpositive estimates, the small accounts hold cash while uncapped experiments
continue. Before any research outcomes mature, a visibly labeled fixed starter long-call policy provides a baseline.

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

The initial registry explores long calls, long puts and long straddles at $5,000, $50,000, $500,000 and $1,000,000 target
debits, 30/120-minute holding horizons, and additional 30/120-minute entry-spacing comparisons. A zero entry spacing permits
each new eligible source observation, subject to unresolved order deduplication. Positions may overlap. The optional
experiment form accepts other sizes, horizons and entry spacing without changing existing histories.

For every decision, the ledger freezes its hypothesis, policy hash, underlying price, original timestamped chain and
provisional expected return. Entry waits for a **strictly later** quote. Quotes must be at most 120 seconds old, not from
the future, and have a usable two-sided spread. Long entries pay ask plus adverse square-root impact; exits receive bid
minus impact. Fees are $0.65 per contract per side. Long premium plus fees is fully cash-collateralized; virtual funding
can provide that cash. Naked shorts, assignment/exercise financing and short-leg structures are not supported by this lab.

There is no order-book depth feed. Capacity is **explicitly estimated** at 0.1% of open interest per contract quote timestamp
(minimum one); usage is shared across all orders in each independent hypothetical book. Repeated snapshots with the same
contract timestamp do not replenish capacity or repeatedly count cumulative daily volume. Orders can partially fill,
and the remainder stays unfilled until a later quote or its 90-minute timeout. The GUI shows requested, filled and
unfilled quantities. Unconstrained requested exposure is not represented as executable profit.

The holding horizon is elapsed clock time. Positions exit on the first later usable quote with sufficient modeled
capacity, so actual holding can exceed the target. Missing, stale or too-wide quotes do not generate imaginary exits.
Expired positions without verified settlement remain visibly unresolved, retain their last marks, and do not train the
model. This is a material limitation; retained marked equity can be stale. The app does not claim these modeled results
prove real execution capacity.

Provider polling and machine scheduling are separate from financial limits: provider collection is paced to a minute,
rotates two underlyings and two expirations per pass, and shares the existing provider rate limiter. Saved observations
are evaluated on a 15-second schedule with rotating batches for orders, positions and proposals. These batches advance
their cursors rather than permanently discarding excess experiments. Collection has its own source-health job and never
postpones the ordinary options collector. Closed markets and missing connections produce readable statuses and retry later.

## Learning and check-ins

This is an initial empirical research engine, not a proven prediction system. Registered hypotheses run before there is
enough evidence for a grade. Only later closed outcomes can update the provisional, shrunk block-mean forecast. Multiple
fills are grouped by underlying/entry day rather than counted as independent proof. Dollars of profit and number of
contracts do not increase the sample's independent evidence count.

Chronological comparisons select among structure families on the earlier half of entry dates and evaluate on later dates.
Training labels must mature before test entries. Advisory promotion requires at least 20 new test days since the previous
promotion and an approximate mean-minus-three-standard-errors hurdle over day means. A promotion prioritizes that structure
and registers a linked doubled-budget sizing experiment; all challengers and losses remain. The comparison reports its
uninvested-cash baseline and separate finite-capital book. Reused rolling comparisons, overlapping exposures, multiple
trials and modeled depth limit confidence; the hurdle is not a formal guarantee of profitability or significance.

Formal comparison starts after at least **4 entry days with completed outcomes and 12 distinct underlying/day blocks**,
with eligible earlier training labels and later held-out entries. These are evidence requirements, not spending or trade
limits. Many fills in one stock on one day can produce dozens of outcomes but only one block. A version-0 formal model
does not mean those outcomes or provisional per-experiment estimates are absent.

**What the system has learned** shows the actual completed research outcome count, block/day counts and minimum progress,
the exact remaining blockers, and the last completed outcome, usable observation and check-in times. Expand **Early
experiment results** for provisional observed returns by structure/holding-time/entry-spacing family. Repeated fills and
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
the experiment results warrant one.

## Verification

Offline tests cover funding after losses, exclusion of deposits from P/L/drawdown, later-quote execution, shared estimated
capacity, partial fills, immutable history, temporal label separation, losing held-out results, policy lineage, independent
collection, CSRF and persisted pauses. Small-account tests also cover different affordable strikes, later-quote price
jumps, no overdrafts/top-ups, retained losses on restart, prior-only research evidence and exclusion of repeated or finite
account outcomes from independent research counts. The `learning-workflows` and `learning-retail` browser fixtures exercise
engine-generated synthetic trades, funding, comparisons, account selection, trade details, custom experiments and downloads
at desktop and phone widths.
These checks validate implementation; they are not live provider or profitability evidence.
