# KESTREL Stocks — PAPER trading

KESTREL Stocks paper-trades stocks and options to test strategies, refine them, and try new ones. **Everything here is
PAPER**: Schwab/Tradier connections supply market data only, with no brokerage order code or real money. The ledger refuses
to open as anything but PAPER.

## Accounts

The separate **Learning** workspace has its own uncapped research book and finite $500,000 comparison book.
Its virtual-funding override and intraday experiments are documented in [STOCKS_LEARNING.md](STOCKS_LEARNING.md).
The strategy accounts described below retain their existing limits and history.

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

## Grading

`desk.stocks.paper.report` compares each account's daily returns with its baseline's (random stocks for stock
strategies, random calls for option strategies) and with SPY, with a stationary-bootstrap interval and one-sided
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
