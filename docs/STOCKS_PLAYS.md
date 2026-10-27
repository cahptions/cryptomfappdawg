# Plays — the options of the day

**Plays** (Stocks dashboard → Plays) ranks today's option ideas in twelve categories. Each play is a quoted,
defined-risk position with everything needed to judge it: the action (buy or sell), the type (call, put, straddle
or credit spread), strike, expiration and days left, the stock price, bid/ask and spread, volume and open interest,
implied volatility and IV rank, the greeks (Δ delta, Γ gamma, Θ theta per day, ν vega per volatility point, net for
multi-leg plays), cost or credit, worst case, best case, break-even, expected value, the one-standard-deviation move
by expiry, and three chances of finishing in the money. Research only: no order can be placed from this desk, and
nothing here sells an uncovered option. **Research, not financial advice.**

Code: `src/desk/stocks/plays.py` (categories, unusual activity, recording and grading), `src/desk/stocks/odds.py`
(the probabilities), `src/desk/stocks/static/plays.js` (the page).

## Three chances, side by side

| View | What it is |
|---|---|
| **Market** | What the option's own price implies: lognormal at its implied volatility, drifting at the risk-free rate. A price, not a forecast. |
| **History** | How often this stock actually moved that far in that many trading sessions, from up to three years of its own closes, rescaled to today's realized volatility. Keeps the stock's fat tails and skew; drops its old trend. |
| **KESTREL** | A forecast. Volatility blends 70% implied and 30% realized variance (implied volatility usually overstates the move that follows). The drift is tilted by the committee's view only as far as the committee's measured information coefficient allows — expected move = IC × z-score × volatility (Grinold), with the IC shrunk by its evidence n / (n + 24 months) and never extrapolated past the one-month horizon it was measured on. With no positive measured IC there is no tilt. |

KESTREL's chance is then **calibrated against how past plays actually finished**. Every recorded play is graded at
expiration; once 30 have resolved, predicted chances are grouped in tenths and pulled toward the observed hit rate
of each group (shrunk by 20 pseudo-observations, so a few outcomes move it only a little). In-the-money events and
straddle moves are calibrated separately. Until then the page says "not yet calibrated".

Also shown: the chance of **profit** at expiry (beyond break-even), the chance the price **touches** the strike
before expiry (reflection formula for geometric Brownian motion; "already in the money" when it has), and the
**expected value** per contract under KESTREL's forecast, after fees.

## The categories

| Category | What qualifies |
|---|---|
| 🎯 **Most likely to finish ITM** | Calls where the committee leans bullish (≥ 58), puts where it leans bearish (< 42); out-of-the-money or at-the-money (\|Δ\| 0.25–0.55), 7–60 days, spread ≤ 15%, open interest ≥ 100. Ranked by KESTREL's chance in the money. |
| ⚖️ **Biggest model edge** | Contracts KESTREL values above their ask: expected payoff per dollar paid ≥ +5%. 7–60 days, \|Δ\| 0.15–0.70, spread ≤ 12%. |
| 🐋 **Unusual activity** | See below. |
| 🏷️ **Cheap volatility** | At-the-money straddle (~30 days) where IV30 ≤ 0.9× realized volatility or IV rank ≤ 20. Chance shown: a move past either break-even. |
| 📅 **Earnings plays** | Results window (estimated from past report dates) starting within 21 days; straddle on the first expiry after it; ranked by the stock's median results-day move ÷ the move options price. |
| 🚀 **Trend calls** | Momentum ≥ 75 and above the 200-day average: call near 0.60 Δ, 30–75 days. |
| 🐻 **Bearish puts** | Valuation ≤ 30, momentum ≤ 35 and below the 200-day, or a red-flag filing: put near 0.45 Δ, 30–75 days. |
| 🔥 **Squeeze calls** | Squeeze score ≥ 60: call near 0.30 Δ, 14–45 days. |
| 💰 **Sell rich premium** | IV rank ≥ 60 or IV ≥ 1.3× realized: **sell** a bull put spread (committee ≥ 50) or bear call spread (≤ 45). Short leg near 0.25 Δ, long leg ~5% further out, 21–60 days, credit ≥ 10% of the width. Shows the credit, the worst case and the chance it expires worthless. |
| 🎰 **Lottos** | Ask $0.05–$0.50, 1–14 days, ≥ 3% out of the money: the most payoff for a one-standard-deviation move, times the chance in the money. Most expire worthless, and the card says so. |
| 🏛️ **LEAPS** | 180+ days, call near 0.75 Δ, on committee ≥ 60 names or broad ETFs: stock-like exposure for less money down. Collection now adds the expiry nearest a year out for this. |
| ⭐ **Your watchlist** | The best-valued contract for each starred stock, in the committee's direction. |

Each category shows its **track record** (plays resolved, how often they finished in the money against what KESTREL
predicted, median return at expiry) and, where a PAPER strategy trades the same idea, that strategy's closed-trade
record. Filters: calls/puts, buy/sell, maximum cost, minimum chance in the money, cards or table; the page
remembers them.

## Unusual activity

Paid "unusual options activity" feeds read every exchange trade print (OPRA) and see sweeps, blocks and which side
initiated each trade. KESTREL reads **quote snapshots** (Schwab or Tradier chains every 15 minutes in market
hours), so it cannot see individual prints or trade direction for certain — and says so on every card. What it
adds instead, and shows for every flag:

1. **Volume ÷ open interest** — more contracts traded than were open means new positions are likely.
2. **Volume ÷ the contract's own average** over the last stored sessions (a contract that always trades 5,000 is
   not unusual at 5,000).
3. **Premium** at the mid (volume × mid × 100).
4. **The whole chain's surge** — today's sampled option volume against that stock's usual.
5. **Where the last trade printed** in the quote: at or above the ask (buyer-paid) or at or below the bid — a
   labelled inference ("bullish lean", "bearish lean" or "direction unknown").
6. **Short-dated out-of-the-money aggression** (≤ 21 days, ≥ 3% out of the money).
7. **IV rising with the volume** versus the previous session (buyers paying up).
8. **Clusters** — three or more unusual contracts of the same type on one stock.

Gates: volume ≥ 250, ≥ $25k at the mid, and volume ≥ open interest or ≥ 4× its own average. Score 0–100; tiers 🐋
Whale (≥ $1M), 🧹 Sweep-like cluster, 📅 Event bet (expires after the next results window), 🆕 Opening (volume ≥ 2×
open interest), ⚡ Unusual.

**Next-session confirmation**: open interest is reported once a day, after the close. The next session, every
contract flagged the session before is checked: how much of its volume became new open interest ("Opened",
"Partly opened", "Mostly closing trades or same-day round trips"), and how its price has moved since. That table
sits at the top of the Unusual activity tab. Over time the category's track record shows how often unusual
activity actually finished in the money — a number paid feeds rarely publish.

## The record

A session's plays are **recorded once**, at the first build after that session closes (16:15 New York), in
`stocks-research.db` (`stk_play_days`, `stk_plays`; append-only). At expiration each play is **graded once** on the
underlying's close (`stk_play_outcomes`): did its event happen (finished in the money, or moved past a straddle's
break-even), what it paid per share, and its net return on the money at risk after fees. A split between the record
and expiry leaves a play unresolved rather than graded on mismatched prices. Intraday, the page shows the live
build from the newest quotes; only the closed session's version is recorded.

## Limits

- Coverage is what the collector has fetched: rotating batches of the most-traded names plus your watchlist,
  SPY and QQQ, and the expiries nearest 30, 60, 90 and 365 days and the nearest weekly. It is not every listed
  contract.
- Greeks and implied volatility are computed here from the bid/ask mid (Black-Scholes, no dividends); providers'
  own greeks are not used.
- Entries in the record use the last quotes of the session (ask to buy, bid to sell). Real fills during the day
  differ.
- Probabilities are model estimates. Until plays have resolved, KESTREL's chance is uncalibrated, and until the
  committee has a positive measured information coefficient it carries no directional tilt. The page states both.

## Updating to get Plays

Plays is on the `claude/funny-rubin-gq99vj` branch (it includes everything from `codex/arbitrage-v1`):

```bash
cd ~/kestrel &&
git fetch origin &&
git switch claude/funny-rubin-gq99vj &&
git pull --ff-only &&
uv sync --frozen &&
uv run desk open --desk stocks --restart
```

The restart is needed once: the Stocks page and server move to UI protocol 5 together. Saved research, PAPER
records and connections are kept. Plays appear after the next options collection pass (within 15 minutes in
market hours).
