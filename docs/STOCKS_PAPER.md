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
Macro inputs (Treasury yields, credit spreads, VIX) are dated market prints and are read as of each replayed day. Short
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
- **Listings.** Monthly expiries (the third Friday, moved off holidays) and a listing-style strike grid stand in for
  the real listings. Open interest is not modeled. Dividends are: every option price and implied volatility reads
  the stock's trailing-12-month cash dividend yield (stored dividends ex by that day, adjusted for later splits), in
  the replay's model, its calibration and the PAPER fills and marks.
- **Choosing never follows a real price.** Contracts are chosen from the model's prices, so one contract's real price
  can't pull a rule toward it; the decision then sees that day's real price where there is one.
- **Downloading.** Each collection pass fetches up to 12 of the contracts the latest replay traded (about 2½ minutes at
  Massive's 5 requests a minute), so all of a replay's contracts arrive within hours. When a batch finishes, the
  automatic replay runs once more to use them (never mid-download). Nothing else in the app uses Massive's option data. `kestrel options-history` shows how many are downloaded and how long the
  rest will take; each option account's replay view shows what share of its held days were priced by real trades.

Strategies whose signal is option data itself are not replayed: **Calls when IV is cheap** (a model built from the
stock's movement can't show options priced cheaply against it), **Cheap earnings straddles** (needs the move options
priced before each report) and **Follow unusual call buying** (needs each day's real volume). They are tested forward
on collected chains. **Squeeze calls** follows short interest, not option data, and replays with it (above).

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
3. **Stops.** A resting stop sells at the stop price if the day's low reached it, or at the open if the stock gapped
   through it.
4. **Expiry.** An option position still open at expiration settles at intrinsic value on that day's close (an
   in-the-money close pays the closing fee).
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
existing positions remain managed. Recovery through missed sessions is simulation and must be identified in the
evaluation; it is not evidence of fills while a laptop was asleep.

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
| Lotto picks · sold at triple | the Plays **Lottos** section's picks recorded when the session closed, in rank order: ask $0.05–$0.50, 2–14 days, ≥ 3% out of the money, volume and open interest ≥ 50, spread ≤ 35%, ranked by the payoff on a one-standard-deviation move per dollar × KESTREL's chance in the money | sells when it is worth 3× (the Plays exit plan); otherwise held to expiry |
| Lotto picks · let them run | the same picks | held to expiry: the rare 20×–100× winner is kept whole, and nothing is sold early |

Both are judged against **Random lottos**, not Random calls. They buy at the next session's ask within the usual limit
(10% or 5 cents above the price seen); a lotto whose price ran further overnight is cancelled, as a limit order would
be. Marks and exits are checked once a day on closing quotes, so a contract that spikes and falls back within a day
is seen at its close.

**Lottos on real past prices** (`desk.stocks.lotto_history`). The modeled replay can't price lottos honestly (their
value is in the far, short-dated tails a model gets wrong), so the lotto accounts are replayed on real prices instead:
Massive's Options Basic archive, two years of daily bars per contract, weeklies included. Each collection pass
downloads a batch (8 requests in market hours, 40 when closed, at Massive's shared 5 a minute): for each of SPY, QQQ,
IWM, AAPL, NVDA, TSLA, AMD, AMZN, META, MSFT, GOOGL and NFLX and every past weekly expiry, the out-of-the-money
contracts listed two weeks before it, then each candidate's real daily bars (about 73 hours of requests in all, so a
few days). The replay then runs KESTREL's Lottos rule (one-standard-deviation payoff per dollar × chance in the money,
from the contract's implied and the stock's realized volatility; no committee tilt) against a random qualifying
contract per stock, sold at triple or held to expiry, $300 a pick, bought at the next session's real open plus 5%
with $0.65 a contract. Each lotto account's Historical replay card shows its version: picks, return on the money put
in, how many expired worthless, reached 3× and 10×, the best pick, and KESTREL against random with a bootstrap range.

## Grading

`desk.stocks.paper.report` compares each account's daily returns with its baseline's (random stocks for stock
strategies, random calls for option strategies, random lottos for the lotto strategies) and with SPY, with a stationary-bootstrap interval and one-sided
p-value, Holm-adjusted across strategies. A strategy is *too early* until it has three months of sessions (six for
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
