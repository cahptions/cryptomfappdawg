# KESTREL Stocks — PAPER trading

KESTREL Stocks paper-trades stocks and options to test strategies, refine them, and try new ones. **Everything here is
PAPER**: Schwab/Tradier connections supply market data only, with no brokerage order code or real money. The ledger refuses
to open as anything but PAPER.

## Accounts

For the product audit and next build priorities, see [STOCKS_PRODUCT_REVIEW.md](STOCKS_PRODUCT_REVIEW.md).

Historical backtests now default to all eligible saved sessions after warm-up: a year (252 sessions) of earlier prices,
so 12-month momentum, 52-week highs and the 200-day average are complete on every replayed day. The main Paper page links directly to the
replay; its date slider and first/previous/next/play controls review archived daily cash, holdings, marks, decisions and
closed trades as they existed on the selected date. Playback is a review of an actual chronological computation, not a
claim that an animation trains a model. The complete run has its own retained ledger under `stocks-replays/`.

The separate **Learning** workspace has its own uncapped research book, finite $500,000 comparison book, and independent
$1,000/$5,000 forward accounts that test affordable trades selected from prior research findings.
Its virtual-funding override and intraday experiments are documented in [STOCKS_LEARNING.md](STOCKS_LEARNING.md).
The strategy accounts described below retain their existing limits and history.

The Paper strategy cards and **Strategy evidence** panel show **Forward PAPER** and **Historical replay** separately.
Completed stock replays use the same session/trade thresholds, daily baseline comparisons, bootstrap statistics and
Holm correction as the forward grader, using the replay's own sessions. The result is stored in its retained archive
with the strategy specification, date range and tested family. An older specification is labeled outdated; use
**Run replay** to open the existing controls. Automatic replay also refreshes older report versions when enabled.
A replay covers every stored session after a year of warm-up, at most the last 1,260 (five years: a session scores
every liquid stock, so about 5 hours, finishing between two closes). Prices before Massive's two years are Schwab's
checked daily history when it has been collected ([method §9](STOCKS_METHOD.md)); the coverage line says where each
source begins and how many stocks were refused. Older years are graded by the monthly rebuild.
Macro inputs (Treasury yields, credit spreads, VIX) are dated market prints and are read as of each replayed day.
The News desk (an observer with no vote) reads what was published by each replayed day: company news counted by
publication day, stored GDELT files and trade actions; the coverage note says from when ([news](STOCKS_NEWS.md)). Short
interest joins the replay automatically once the stored FINRA settlements cover the replayed window (24 or more
twice-monthly settlements, the latest published by the first replayed day, each counted 12 days after settlement, with
no gap or partial settlement after it); **Squeeze runners** and **Squeeze calls** replay then. Until it does, they say so
and short interest is left out. Lab strategies replay too; they were chosen on the same rebuilt months, so their replay
is in-sample and says so.

### Option strategies in the historical replay

The option strategies whose signal comes from the stock (**Random calls**, **Momentum call spreads**, **Bear put
spreads**) replay as far back as stock prices go, through the same PAPER engine, fills, limits and exits as forward
trading (`desk.stocks.option_model`):

- **Real prices first.** Massive's free Options Basic plan has every contract's daily closing trade for the last 2
  years. On a day a held contract traded, that is its price. On a quiet day, its last trade's implied volatility (within
  a week) prices it at that day's stock price.
- **The model everywhere else.** Before Massive's window, or for a contract that never traded, the price is
  Black-Scholes at the stock's realized volatility (half 20-day, half 60-day) times how options on that stock have been
  priced against it, with a put skew and the stock's typical quoted spread. All three are calibrated from the chains
  this app collected and from Massive's real prices on an expanding window: refit at the start of each replayed month
  from observations dated before it, so no session is priced with what options did later, falling back to stated
  market-wide values until there are enough.
- **Chains and listings.** A replayed day's chain is, in order: **KESTREL's own collected chain** for that stock and
  session where one is stored (every collected session is kept within the storage budget; real bid/ask, volume and open interest, exactly what forward PAPER
  fills from; a contract held from earlier that it lacks keeps its rebuilt quote); else one **rebuilt on Massive's real
  listing** for that month (the real expiries and strikes within 20% of the price, downloaded through the Massive
  queue for each stock and month the latest replay traded, about one request each); else one rebuilt on the
  **stand-in**: monthly expiries (the third Friday, moved off holidays) and a listing-style strike grid. The replay's
  limitations say how many stock-days used each, and each option account's price mix counts KESTREL's own quotes.
  Open interest isn't stored historically: where Massive was asked about the days before a decision, a contract that
  traded fewer than 10 contracts in the 10 days up to it is passed over as illiquid, and any other rebuilt contract is
  assumed to clear the rules' 100-contract floor (the replay reports how many were checked). Option strategies trade
  only the stocks whose chains this app has collected at some point. Dividends: every option price and implied
  volatility reads the stock's trailing-12-month cash dividend yield (stored dividends ex by that day, adjusted for
  later splits), in the replay's model, its calibration and the PAPER fills and marks.
- **Choosing never follows a real price.** Rebuilt contracts are chosen from the model's prices, so one contract's
  real price can't pull a rule toward it; the decision then sees that day's real price where there is one. On a
  collected day every contract is real, so the rule chooses from the real quotes as forward PAPER does.
- **Downloading.** Every Massive option download goes through one queue (`desk.stocks.massive_options`) with one
  budget a collection pass: 8 requests while the market is open, 40 while it is closed (Massive allows 5 a minute for
  the whole account). Each task in turn gets an equal share of what is left and hands back what it does not need: the
  contracts the latest replay traded first, then the IV history (`desk.stocks.iv_history`), then the straddles before
  past reports (`desk.stocks.real_options`), then the real monthly listings of the stocks the replay trades, then the
  Lottos history (`desk.stocks.lotto_history`). When a batch of the replay's contracts finishes, the automatic replay runs once more
  to use them (never mid-download). `kestrel options-history` shows how many are downloaded and how long the rest will
  take; each option account's replay view shows what share of its held days were priced by real trades.

### Option-data strategies on real past prices

**Calls when IV is cheap** and **Cheap earnings straddles** read option prices themselves, which a model built from
the stock's movement can't show, so they are not replayed on modeled prices. Inside Massive's 2-year window they
replay on **real prices** instead (`desk.stocks.real_options`), beside the modeled replay, in their own accounts:

- **Signals:** each replayed session, the strategy's own rule reads that day's committee, momentum and results
  calendar from the stock replay, with real option data: IV30 from the IV history (solved from real closing trades)
  and, before a report, the expected move of the straddle on the first expiry after the results window, from its call's
  and put's real closing trades.
- **Contracts:** the call is the monthly call downloaded for the IV history nearest 0.50 delta (0.35-0.65) with 30-60
  days left; the straddle is that report's call and put. **Random calls on real prices** (the same kind of call on a
  random stock that has one that day) is the baseline both are compared with, by a bootstrap over days.
- **Accounting** (as the lottos on real prices): $1,000 a trade (2% of $50,000), at most 5 open and one per stock,
  bought at the next session's real opening trade plus 4% and $0.65 a contract a leg; later closes less 4% and fees
  are checked against the rule's take-profit, stop and dates (a straddle leaves the second session after the results
  window); still open at expiry, it settles at intrinsic value. A day a leg did not trade is skipped, never filled in.
- **Downloading:** the straddles come through the Massive queue: the replay lists every report the rule looked at
  (window centre 2-10 days ahead, 4+ past reports), and each costs 3 requests (the listing as of the first day the
  rule saw it, then the call's and put's bars). The next replay uses them.

The Paper card shows trades, the return on the money put in, the win rate, the best trade, random calls on real prices
and the comparison, with the real data it had (stocks with an IV history, reports downloaded of those wanted). It is a
replay: it never changes a forward grade or unlocks anything. **Follow unusual call buying** needs each day's
whole-chain volume (about 450,000 requests) and is tested forward on collected chains only. **Squeeze calls** follows
short interest, not option data, and replays with it (above).

Historical rules were designed with knowledge of past data, and the stored listing universe/filing history can be
incomplete. A favorable replay is exploratory reconstruction, not fresh prospective evidence. Replayed days never
increase forward session counts, change forward grades, unlock trades or promote a model. **Inspect replay** opens the
existing session slider, trades and playback; neither grade requires terminal commands.

Every strategy trades its own paper account, so strategies never compete for cash and each has a clean record.
Stock accounts start with $10,000 and option accounts with $50,000 (so that 2% of the account buys one contract of
most liquid names). Results are compared in percentages, but contract minimums/capacity differ by capital:
these experiments do not establish feasibility for the $500 crypto reference account. Both are set in
`config/default.toml` (`[stocks] paper_*`).

| Limit | Default |
|---|---|
| One stock position | at most 20% of account equity; each strategy may use less |
| Planned stock stop risk per ticket | at most 2% of account equity; gaps can lose more |
| Stock ticket capacity | at most 0.5% of observed daily dollar volume |
| One options trade | risks at most 2% of the account (the debit plus fees) |
| All open option premium | at most 10% of the account |
| Open positions | at most 30 per account |

Options strategies use long calls, long puts, long straddles and debit spreads. The PAPER model budgets their
initial debit and fees, and sells no naked options. Its simplified intrinsic settlement does not model early
assignment, exercise financing or every operational loss; it is not a guarantee about a real options account.

## How a trade happens

The desk works from end-of-day data. After a session closes and its prices, option chains and analysis are stored,
the engine steps every account through that session (`desk.stocks.paper.engine`):

1. **Fill yesterday's orders.** Exits first, then entries. A stock fills at **today's open** plus slippage (0.05% for
   names trading $50M+ a day, 0.15% otherwise). An option fills at **today's chain snapshot**: long legs at the ask,
   short legs at the bid, and only if the spread is fair (≤ 35% of the mid) and the ask hasn't run past the order's
   limit (10% or 5 cents above what the decision saw). Otherwise the order is cancelled, as a limit order would be.
   Orders that can't fill for 5 sessions (stocks) or 3 (options) are cancelled.
   An order created after a session's opening waits for a future opening. Option entries require dated quotes
   after the decision; a captured snapshot from before the decision cannot fill that order.
2. **Corporate actions.** A split changes the share count from its execution date; cash dividends are paid on the
   ex-date to positions held before it. An option whose underlying splits is closed at its last mark.
   A position of bought contracts only is never sold for no more than its exit fees, here or at an exit order: it is
   written off at zero instead (no sale, no fee), so it never loses more than it cost (from 2026-11-13; it lost the
   exit fee besides, up to $0.65 a contract, before; earlier exits are kept as recorded).
3. **Stops.** A resting stop sells at the stop price if the day's low reached it, or at the open if the stock gapped
   through it.
4. **Expiry.** An option position still open at expiration settles at intrinsic value on the close of its last
   trading day (the session before an expiry that falls on a market holiday); a close in the money by more than the
   closing fee pays it, and a position of bought contracts in the money by less lapses at no cost. When a pass missed
   the expiry, it settles on that day's own close as soon as it is stored, never on an earlier one (until 2026-11-13
   it took the last close stored before the expiry when the expiry's bar was missing).
5. **Marks.** Stocks at the close; options at the mid (a contract missing from the chain is priced with Black-Scholes
   at its last implied volatility and flagged as modelled). Trailing stops ratchet up.
6. **Decisions.** Exits (time, take-profit, stop, days to expiry, results passed, monthly rebalance) and entries from
   today's analysis become orders for the **next** session. Nothing decided on a close fills at that close.
7. **Equity.** Each account's cash + positions is recorded for the day. A day is stepped once and never backwards.

Costs: stock commissions are modeled as $0, separately from crypto fees. Stock sales retain approximate SEC Section 31
and FINRA TAF costs using the existing **2025** constants. These are not a verified current fee schedule. Options retain
$0.65 per contract per leg each way. Bid/ask execution and slippage remain costs even when commissions are zero.
Before expiry, absent quotes and absent usable implied volatility produce an unavailable value, not an invented
intrinsic-value fill. Model-based option exits are explicitly labeled; they are not evidence of executable exits.

Every decision (ENTER, EXIT, and SKIP with its reason), order, fill, cash movement and daily equity is append-only.
A position's entry never changes and a closed position never changes (SQLite triggers refuse it).
The whole ledger session commits in one transaction. STOP cancels pending entries and suppresses new ones while
existing positions remain managed. Recovery through missed sessions is simulation, not evidence of fills while a
laptop was asleep: a session stepped without its own analysis (exits, stops, expiries and marks only) is recorded as a
RECOVERY event, and the report counts and lists each account's recovery sessions, which the Strategy evidence card
shows beside its forward sessions. Ledgers from before that event are read the same way: a session stepped in the same
pass as a later one was caught up.

## The strategies

Each is preregistered: hypothesis, rule and exits are frozen by version in `stk_specs` before it trades.

**Baselines** — what the others must beat:

| Strategy | Rule |
|---|---|
| Hold SPY | buy SPY once, hold |
| 10 random stocks | 10 liquid names drawn at random (reproducibly) each month |
| Random calls | an at-the-money 30–60 day call on a random optioned name; same exits as the call strategies |
| Random lottos | 12 random optioned names a day, each a random contract passing the Lottos filters below; held to expiry |

**Stocks, rebalanced monthly** (top 10 at the open after each month's last session; names that drop out are sold):
the committee's top 10; the Undervalued, Quality at a fair price and Momentum lists; and three combinations —
**value + momentum** (valuation ≥ 60 and momentum ≥ 60), **quality + momentum** (quality ≥ 60 and momentum ≥ 60),
**cheap with a catalyst** (valuation ≥ 65, earnings ≥ 60, quality ≥ 40).

**Stocks, on events (v2)**: up to 10 open, except squeeze runners (5). Desired tickets are 15% of equity for earnings,
insiders and breakouts, and 7.5% for squeezes. Each ticket is reduced by cash, the account-relative position cap,
planned stop risk and liquidity capacity. At the defaults, insider tickets are effectively capped at 10% because
their 20% stop distance consumes the 2% planned risk budget. Stops do not guarantee a maximum loss.
The same limits are checked again when a pending order fills; there is no leverage.

| Strategy | Enters | Exits |
|---|---|---|
| Post-earnings drift | results within 5 days, +3% vs the market on results day, earnings desk ≥ 60 | 60 sessions, 12% stop |
| Insider clusters | 3+ insiders buying, or buys ≥ 5 bp of market value | 126 sessions, 20% stop |
| 52-week-high breakouts | new high on ≥ 1.5× volume, momentum ≥ 70 | 42 sessions, 10% trailing stop |
| Squeeze runners | squeeze score ≥ 60 and up on the week | 10 sessions, 10% stop |

The other stock portfolios retain their own allocation rules; a fully invested hold baseline remains fully invested.
These larger event tickets are an unvalidated PAPER experiment requested by the operator, not a response to proven
profitability or calibrated confidence. They increase concentration. They do not make the daily engine intraday:
new close-based signals still wait for a later session's opening, and the existing signal requirements remain.
Old positions and cash history are preserved. Accounts containing earlier specification decisions display a mixed-version
warning and cannot earn a current-version grade from those combined results. Version-specific evaluation remains required.

**Options** (2% each unless noted):

| Strategy | Enters | Structure | Exits |
|---|---|---|---|
| Calls when IV is cheap | committee ≥ 60, momentum ≥ 55, IV30 ≤ 1.05× realized | 0.50Δ call, 30–60 days | +100%, −50%, 21 days left |
| Momentum call spreads | momentum ≥ 80, above the 200-day | 0.55Δ / 0.30Δ call spread, 30–60 days | +75%, −50%, 14 days left |
| Bear put spreads | valuation ≤ 30, momentum ≤ 35, below the 200-day | 0.50Δ / 0.25Δ put spread, 30–60 days | +75%, −50%, 14 days left |
| Cheap earnings straddles | results in 2–10 days and the straddle prices ≤ 85% of its median results-day move (4+ reports) | ATM straddle, first expiry after the report | the session after results, +100%, −60% |
| Squeeze calls | squeeze score ≥ 60 (1% each) | 0.30Δ call, 14–45 days | +200%, −60%, 7 days left |
| Follow unusual call buying | ≥ 1,000 contracts, ≥ 5× usual, ≥ $100k premium (1% each) | the same contract | +100%, −50%, 5 days left |

**Lottos** (added 2026-11-04 at the operator's request, after a Plays lotto showed a 7,800% gain). Cheap, short-dated,
far out-of-the-money contracts: most expire worthless, a few pay many times their cost, so the test is whether the
winners pay for all the losers after fees, and whether KESTREL's picks do better than chance. Every pick gets the same
dollars, 0.6% of the account ($300 of $50,000, so ten contracts at $0.30 or about forty at $0.07, $0.65 a contract in
fees, which on a $0.07 contract is 9% of its cost); up to 20 open at once, inside the 10% options-at-risk cap.

| Strategy | Enters | Exits |
|---|---|---|
| Lotto picks · sold at triple | the Plays **Lottos** section's picks recorded when the session closed, in rank order: ask $0.05–$0.50, 2–14 days, ≥ 3% out of the money, volume and open interest ≥ 50, spread ≤ 35%, ranked by the payoff on a one-standard-deviation move per dollar × KESTREL's chance in the money | sells when it is worth 3× (the Plays exit plan), checked on Schwab quotes every five minutes in the session and on the close; otherwise held to expiry |
| Lotto picks · let them run | the same picks | held to expiry: the rare 20×–100× winner is kept whole, and nothing is sold early |

Both are judged against **Random lottos**, not Random calls. They buy at the next session's ask within the usual limit
(10% or 5 cents above the price seen); a lotto whose price ran further overnight is cancelled, as a limit order would
be. Marks and exits are checked on closing quotes, and **Lotto picks · sold at triple** (version 2, 2026-11-08) also
checks its take-profit during the session (`desk.stocks.paper.intraday`): each Plays tracking pass, every five
minutes, quotes the held contracts through Schwab `/quotes` (one request for up to 100) and, when a fresh bid (under
five minutes old, after the entry) is worth triple, sells there with the usual fees, recording the decision, the order
and its fill with the quote's own time. A spike between passes can still be missed. Its version-1 positions keep their
record; the account shows its mixed-version warning until the new version's own record grades.

**Lottos on real past prices** (`desk.stocks.lotto_history`). The modeled replay can't price lottos honestly (their
value is in the far, short-dated tails a model gets wrong), so the lotto accounts are replayed on real prices instead:
Massive's Options Basic archive, two years of daily bars per contract, weeklies included. Each collection pass
downloads a batch (its share of the one Massive queue, 8 requests a pass in market hours and 40 when closed): for each of SPY, QQQ,
IWM, AAPL, NVDA, TSLA, AMD, AMZN, META, MSFT, GOOGL and NFLX and every past weekly expiry, the out-of-the-money
contracts listed two weeks before it, then each candidate's real daily bars (about 73 hours of requests in all, so a
few days). The replay then runs KESTREL's Lottos rule (one-standard-deviation payoff per dollar × chance in the money,
from the contract's implied and the stock's realized volatility; no committee tilt) against a random qualifying
contract per stock, sold at triple or held to expiry, $300 a pick, bought at the next session's real open plus 5%
with $0.65 a contract. "Sold at triple" is tested two ways: on a later **close** less 5% (the main result), and, as its
own clearly labelled variant, by a resting limit at 3× the cost counted as filled on the first day the contract's
**daily high** reached it. A daily high is one trade at some moment of the day, not proof the whole position could
have been sold there, so the high variant is an upper bound shown beside the close-based one, never in its place. Each
lotto account's Historical replay card shows its version: picks, return on the money put in, how many expired
worthless, reached 3× and 10×, the best pick, KESTREL against random with a bootstrap range, and for the sold-at-triple
account the daily-high upper bound.

### Preregistered option research on real prices

Four research-backed option rules were written down on 2026-11-08, before any result existed
(`desk.stocks.option_research.RULES`, with a version hash shown beside the results), and replay only on the real data
the IV history already downloads (no extra requests): each month's at-the-money call and put per stock, their real
daily trades, and IV30 solved from them. Accounting as the real-price replays above ($1,000 a long trade, next real
open plus 4% and $0.65 a contract a leg, later real closes less 4% and fees, intrinsic value at expiry).

| Rule | Source | What it does |
|---|---|---|
| Vol-gap straddles | Goyal & Saretto (2009) | buy the monthly at-the-money straddle (30-60 days) when 20-day realized volatility is at least 1.25× IV30; at most 5 open, one per stock; +50% / −40% / 21 days to expiry; against a random stock's straddle the same day |
| SPY put-write | Whaley (2002), CBOE PutWrite | each monthly expiry, sell SPY's at-the-money put on the first session with 45 days or fewer left (next open less 4% and fees), cash-secured, held to expiry; against holding SPY over the same days |
| IV spread | Cremers & Weinbaum (2010) | each week's last session, call IV − put IV at the same strike (20-45 days); with 10+ stocks, buy the 5 highest and sell the 5 lowest at the next open for 5 sessions; the long-minus-short weekly return with a bootstrap range |
| Regime chooser | volatility risk premium by regime | on each SPY put-write day: IV rank ≥ 50 writes the put, below 50 buys SPY's straddle; against always writing the put |

They are recomputed at most every six hours while the IV history downloads and shown on the Paper page under
"Preregistered option research". They are research only: never traded, never a forward grade, never a change to a
model. The put-write's cash earns nothing here, which understates it slightly.

## Grading

`desk.stocks.paper.report` compares each account's daily returns with its baseline's (random stocks for stock
strategies, random calls for option strategies, random lottos for the lotto strategies) and with SPY's total return
(each close plus any dividend that went ex that day, reinvested, since the accounts are paid dividends too), with a
stationary-bootstrap interval and one-sided p-value, Holm-adjusted across strategies. A strategy is *too early* until it has three months of sessions (six for
monthly portfolios) and, for event and options strategies, 20 closed trades. Then: *working* (Holm p < 0.05),
*promising* (p < 0.10), *backwards* (p > 0.95) or *not working*. The report also shows return, drawdown, volatility,
Sharpe, win rate, profit factor, average holding time and the best and worst trades.

## The learning lab — new strategies

`desk.stocks.paper.lab` looks for better combinations than the ones above, without fooling itself:

- **Candidates**: every combination of one, two or three of six desks (valuation, quality, growth, momentum,
  earnings, risk), holding the top 10 or 20 by their average score — 82 strategies.
- **Tested on the rebuilt months** (`desk.stocks.validation`): bought at the open after each month's last session,
  held a month, minus a 0.2% round trip, against SPY.
- **Picking is tested too**: at each month the lab picks the combination with the best record over months whose
  returns were complete by then, and records that pick's next month. Only if those picks beat the committee's own top
  10 over at least 12 unseen months (and beat SPY, one-sided p < 0.20) is the current best combination promoted.
- **Promoted strategies PAPER trade** like the others (`learning = "paper"`; `"record"` only records the lab's
  results; `"off"` disables it), at most three at a time. A newer one retires the oldest: it stops opening positions
  and lets its open ones exit. Promoted strategies are kept forever in `stk_lab_strategies` and never change.

Crowd and options signals have no history to test on, so the lab can't search them yet; their strategies above prove
themselves in PAPER.
