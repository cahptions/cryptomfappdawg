# Plays — the options of the day

**Plays** (Stocks dashboard → Plays) ranks today's option ideas in twelve categories. Each play is a quoted,
defined-risk position with everything needed to judge it: the action (buy or sell), the type (call, put, straddle
or credit spread), strike, expiration and days left, the stock price, bid/ask and spread, volume and open interest,
implied volatility and IV rank, the greeks (Δ delta, Γ gamma, Θ theta per day, ν vega per volatility point, net for
multi-leg plays), cost or credit, worst case, best case, break-even, expected value, the one-standard-deviation move
by expiry, and three chances of finishing in the money. Research only: no order can be placed from this desk, and
nothing here sells an uncovered option. **Research, not financial advice.**

Code: `src/desk/stocks/plays.py` (categories, unusual activity, recording and grading), `src/desk/stocks/odds.py`
(the probabilities), `src/desk/stocks/static/plays.js` (the page); history, performance and learning are described
below.

## Personal selections and exact-play details

Click a **BUY/SELL badge** to open an adjacent confirmation window and choose a whole-contract quantity. Confirming
creates a personal selection in **Plays → My tracked plays**, separate from the automatic research cohorts, strategy
PAPER ledgers and uncapped Learning account. It sends **no brokerage order**. Repeated submission of the same
confirmation is idempotent. Ideas, confirmations and lifecycle events are append-only and survive restarts.

Premarket confirmations become eligible at **9:30 AM New York time on the next trading session**, not 9:45. A
confirmation made during the regular session becomes eligible immediately and cannot backdate to that morning's
open. After close, weekends and exchange holidays queue for the next session. The default is the opening session;
**no validated adaptive entry-timing model is active**. The dashboard must be running with collection enabled to
capture an entry. This calendar currently assumes a 16:00 close and does not implement exchange half days.

On a tracking pass, every exact leg must have a positive, noncrossed bid/ask, a provider timestamp at or after
eligibility, and a quote age of at most two minutes. Entry uses asks for bought legs and bids for sold legs. The
actual capture time is recorded, so a missed opening is never reconstructed as an opening fill. These are PAPER
quote references, **not depth-aware execution simulations or proof a large order could fill**. Existing tracker
request/rate budgets still apply; the nominal five-minute quote cadence can miss a faster move.

Queued selections can be cancelled; open selections can request a PAPER close, recorded only from a subsequent
fresh regular-hours liquidation quote. Original entry quotes and quantity remain saved. Expired open selections
receive a separately labelled intrinsic expiry comparison when the official underlying close becomes available;
the app performs no exercise or assignment. Contracts expiring before any valid entry are cancelled without an
invented fill. Pause/STOP continues to pause source collection and tracking.

The **Open play, P/L & exact-leg calculator** link opens a stable, dedicated detail page. The P/L chart follows the
retained quote path from the idea reference, or from the captured personal entry, with the best observed earlier
exit and the expiry comparison. Quantity defaults to the personal selection's size and scales every leg. Active
details check saved observations every fifteen seconds, preserving chart quantity, range and inspected timestamp.

The calculator preselects the exact OCC contracts, call/put, strikes, expirations and buy/sell sides, including
multi-leg spreads. It uses the **dated original idea snapshot** for scenario pricing, clearly distinct from the
personal entry history. It never silently swaps in the first strike from today's chain. Frozen idea legs remain
available after raw chain retention expires; other strikes/expirations require the same session's saved chain.
Calculator edits and chart quantity changes are hypothetical and do not change a saved selection.

Code: `play_selections.py`, `static/play_workflow.js`; research migration `0010_personal_plays.sql`.

## Profit targets, stock prices and understandable Greeks

Cards show the underlying price next to the ticker, as of the play's quote. Dedicated saved details label their
price at identification. Separate meters show the original event odds, **profit at expiry**, and a **+50% return on
maximum risk at expiry**, with +25%/+100% targets in the expandable explanation. `play_targets.py` integrates the
favorable regions of the exact piecewise terminal payoff under the recorded lognormal forecast, after opening
fees. These target probabilities are **model estimates, not empirically calibrated target-hit rates**; they do
not estimate selling at a peak or hitting a target before expiry. The original ITM calibration does not calibrate
these profit targets. Older saved ideas may not contain target estimates.

Finite credit spreads can show the probability of maximum expiry gain. Long calls have unbounded maximum gain;
long puts reach their theoretical maximum only with the underlying at zero. The UI explains these cases instead
of fabricating a useful maximum-gain percentage.

Each card includes a plain-language guide: **Δ Delta** is sensitivity to a $1 stock rise; **Γ Gamma** is Delta's
change for that rise; **Θ Theta** is value change per calendar day; **ν Vega** is value change for one percentage
point of IV (e.g. 40% → 41%). Dollar examples use the standard 100-share multiplier and signed net spread Greeks.
They are local sensitivities with other inputs held constant, not fixed forecasts across large moves.

## Company profiles

Stock profiles automatically queue SEC company information alongside the viewed-symbol history request. The
**About the company** section includes a sourced annual business excerpt, available sector/industry, headquarters,
phone, website, fiscal year-end and filer category. The annual filing link/date and metadata retrieval date are
visible. ETFs have a fund-specific explanation rather than invented company fundamentals.

A separate bounded background task downloads at most one requested profile per pass, sharing the existing SEC rate
limiter and contact-email setting. Profiles cache for thirty days, failures back off ten minutes, and a refresh
preserves the last good description. The GUI's **Refresh company information** queues another attempt; missing
SEC identification directs to Settings. There is no new paid API requirement. The extractor rejects table-of-
contents entries and uncertain section boundaries instead of inventing a company description. Unsupported filings
or unavailable fields remain explicitly missing. Display-only profile text never enters historical trading features.

Code: `company_profiles.py`, SEC submissions parser and market migration `0006_company_profiles.sql`.

## Three chances, side by side

| View | What it is |
|---|---|
| **Market** | What the option's own price implies: lognormal at its implied volatility, drifting at the risk-free rate. A price, not a forecast. |
| **History** | How often this stock actually moved that far in that many trading sessions, from up to three years of its own closes, rescaled to today's realized volatility. Keeps the stock's fat tails and skew; drops its old trend. |
| **KESTREL** | A forecast. Volatility blends 70% implied and 30% realized variance (implied volatility usually overstates the move that follows). The drift is tilted by the committee's view only as far as the committee's measured information coefficient allows — expected move = IC × z-score × volatility (Grinold), with the IC shrunk by its evidence n / (n + 24 months) and never extrapolated past the one-month horizon it was measured on. With no positive measured IC there is no tilt. |

KESTREL's chance is then **calibrated against how past plays actually finished**. Every recorded play is graded at
expiration; once 30 have resolved, predicted chances are grouped in tenths and pulled toward the observed hit rate
of each group (shrunk by 20 pseudo-observations, so a few outcomes move it only a little). In-the-money events and
straddle moves are calibrated separately, and each category and days-to-expiry group gets its own calibration once
it has 30 resolved plays (see Learning). Until then the page says "not yet calibrated".

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
`stocks-research.db` (`stk_play_days`, `stk_plays`; append-only), each with the rule version it was picked under. At
expiration each play is **graded once** on the underlying's close (`stk_play_outcomes`): did its event happen
(finished in the money, or moved past a straddle's break-even), what it paid per share, and its net return on the
money at risk after fees. A split between the record and expiry leaves a play unresolved rather than graded on
mismatched prices. Intraday, the page shows the live build from the newest quotes; only the closed session's version
is recorded.

## History: what every pick did afterwards

History defaults to **All identified plays**: the union of automatically saved discoveries and older closing picks,
including ideas that disappeared from the rankings before the close or appeared after an incomplete closing
snapshot was frozen. **No click, confirmation or personal PAPER selection is needed.** Each distinct contract
structure is kept once per category per identification day, with its original saved reference. Identification dates
use New York time; a stale board first viewed the following day cannot backdate a new discovery to yesterday.

**Closing snapshot** switches back to the immutable evaluation cohort described below. Its entries, calibration,
twins and learning results are not rewritten to make the new archive look better. A closing category frozen empty
does not prevent the all-identified view from showing genuine discoveries. My tracked plays remains the separate
list of explicit user confirmations. Coverage states how many plays were saved, have observations, await quotes,
and belong to the closing snapshot. The archive does not compare its full set with an unmatched closing-only twin
subset.

Unexpired archived contracts stay in the shared quote rotation even after leaving today's board. Daily marking,
gap estimates, expiry comparisons and split quarantine also cover them, under the existing provider budgets.
Original intraday quote snapshots are not relabelled as closing quotes. An archive entry cannot receive marks
before the time it was identified. Actual bid/ask liquidation values and fees drive progress; estimates stay labelled.
History checks saved observations every twenty seconds while visible, preserving scroll and date input focus.

On startup and tracking passes, the app automatically recovers up to 500 older first sightings per pass from their
original stored contracts, entry and timestamp. This repairs the earlier two-LEAPS-only failure wherever those
sightings were retained. Missing original ranks, odds and stock-price snapshots remain unavailable; today's model
is not used to invent historical recommendations. Unreadable sightings are recorded as skipped rather than
blocking other recovery. Picks or option quotes that were never stored cannot be recovered by this process.

Archive implementation: `play_archive.py`; `0011_play_archive.sql` adds lookup indexes and the append-only recovery
skip audit. `stk_play_ideas` preserves the original complete snapshots; the older `stk_play_first_seen` table is the
recovery source. Archive daily marks use a separate category namespace and do not enter closing-cohort grading.

### Closing snapshot

Plays → **History** shows any recorded session (Today, Yesterday, 1 week, 1 month, 3 months, 1 year, or a date; a date
without picks shows the nearest earlier session). Every play is listed with its entry, the price when it was first
shown, its latest value and P/L (live from today's quotes while it is open), its peak and low, days held, whether it is
open, finished in the money, expired worthless, won or lost, and a sparkline; each category and the session get an
average, median, share in profit, best and worst, "$100 into each play" and the same for the random twins.
Click a play for its **P/L chart** from the session it was picked: 1D and 1W from five-minute marks, 1M and All from
daily closes through expiry (or through today for a contract that has not expired; nothing is drawn past expiry), in %
or $, bought at the close or when first shown, with a crosshair (time, value, P/L, stock price), entry/peak/low/expiry
markers and the random twin on request. Beside it: the greeks when picked and now (KESTREL's and Schwab's), why it was
picked, what happened, and every daily mark.

Two bases, labelled: **at the close** is the recorded entry (the ask of the session's last quotes); **when first
shown** is the ask when the play first appeared on the live board that session (`stk_play_first_seen`, recorded only
while the dashboard was running during the session).

### Selling before expiry

The per-play chart defaults to dollar P/L across the full recorded holding window. **All and 1M retain the available
five-minute observations**, rather than reducing them to daily closes and hiding a profitable spike. Month ranges end
at the play's last observation, so an old expired play remains inspectable. The chart does not add invented movement
between sparse observations; estimates remain dashed, and histories with fewer than three points explain the limit.
The default chart leaves estimated intervals as gaps. **Include estimated gaps** overlays them as dashed amber lines;
their values stay separate from the observed best exit and cannot dominate its default chart scale.

Three cards show the P/L at the inspected time, the **best observed exit before expiry**, and the latest value or expiry
outcome for comparison. The best exit has a date/time and a chart marker; **Show best exit on chart** selects it. An
editable contract quantity scales dollar P/L and fees already included per contract; for a multi-leg structure the
quantity applies to every leg. Changing quantity does not place orders or change a PAPER account. Cursor/touch
inspection also shows a rectangular tooltip. The first-shown entry is the default when it was actually recorded.

Best observed means the largest net liquidation P/L among retained quoted observations after that entry and before
settlement: longs sell at bid and shorts buy back at ask, with fees. It can be negative when no observed exit made
money. Quotes before the chosen entry, future observations, settlement values and modelled/estimated/provisional
highs cannot win this ranking. Multi-leg paths reject combinations carrying a leg forward by more than five minutes.
Estimated highs are reported separately, and expiry-only data cannot establish an earlier quoted sale opportunity.

This is hindsight, **not a prediction of the peak or proof an order could fill there**. It is the best retained
observation, not the true maximum across every market trade. Five-minute coverage can miss a faster spike; after
intraday retention expires only the stored daily closes remain. No historical option quotes are manufactured from
stock prices, and the app does not perform assignment or exercise when displaying an expiry comparison.

### Live tracking (Schwab)

Code: `src/desk/stocks/play_tracking.py`. The dashboard follows every leg of every unexpired recorded play, today's
live board and the twins. In market hours a pass every five minutes quotes what is due from Schwab's `/quotes`
endpoint, 100 option symbols a request (stored OCC symbols are padded to Schwab's six-character root, and each answer
is checked against its strike, call/put, expiry, 100 multiplier and underlying; adjusted contracts are dropped), with
Schwab's own IV and greeks. Contracts it misses fall back to the selected provider's per-expiry chains; anything the
research collector fetched since the last pass is used free. Priorities: the live board, plays from the last five
sessions and anything expiring within a week every pass; older plays every 15 minutes; twins hourly; from 15:50 every
contract once more for its close. A pass spends at most 40 requests (under a tenth of the 90-a-minute Schwab budget,
which is shared with research collection). Streaming quotes are not used: the streamer needs Schwab's account-scoped
user preferences, and this integration requests market data only.

Five minutes after the close the session folds into one packed intraday row per contract (kept at least 30 days —
the database refuses an earlier delete — then compacted after 35) and a permanent daily close with the closing
bid/ask, KESTREL's IV and greeks solved from the mid beside Schwab's, and the day's high and low mid. System → Plays
tracking shows what is tracked, quote freshness by schedule, anything waiting, requests used, the quotes endpoint's
verified state and storage used.

### Marks, gaps and settlement

Code: `src/desk/stocks/play_marks.py`. Every play and twin gets one append-only mark per session: **liquidation
value** (long legs at the bid, short legs bought back at the ask — the honest "if I closed now"), mid value, and P/L
per contract after fees (opening fees always; closing fees unless it settled) as a share of the money at risk. Its
status is one of:

| Status | Meaning | Graded and learned from? |
|---|---|---|
| **quoted** | every leg had a closing quote (live, or the play's own recorded quote on its first day) | yes |
| **settled** | expiration: intrinsic value on the stock's official close (matches the expiry grade) | yes |
| **modelled** | tracking ran that session but a leg had no closing quote: Black-Scholes on the stock's close at that leg's last IV | no |
| **estimated** | a gap (the laptop was off): Black-Scholes on the stock's real closes, IV interpolated between the quotes either side | no |
| *provisional* | a gap no later quote brackets yet: IV held at the last quote; replaced once a quote or the expiry arrives | no, and not permanent |

**Gap repair runs whenever the dashboard is open** (every pass after the close, hourly while trading): stock closes
come from the stored daily bars, else Schwab's daily price history (the smallest documented period that reaches the
gap; kept only if it agrees with stored closes and postdates the last known split), else the underlying Schwab
reported with the closing quotes. Expirations inside a gap are settled exactly on the official close. For gap sessions
Schwab still has minute bars for (about five), a five-minute path is estimated too. Estimates across a results filing,
a results window or a stock move beyond four standard deviations are labelled unreliable. Holidays are not sessions
and are never filled; a halted stock simply has no row that day. A split ends a play's record the session before it
(quarantined, with the reason shown); a stock without a price for ten sessions is quarantined as halted or delisted;
short legs deep in the money with under $0.05 of time value carry an early-assignment warning. History shows
"Repairing N plays" while this runs. Nothing an estimate says is ever graded or learned from.

### Twins

Every recorded play gets a **random twin**: a seeded draw among the other stocks with a contract of the same
structure and expiry (or the nearest within a week) at the nearest delta (`stk_play_twins`). Twins are tracked and
marked like plays. They answer "was it the pick, or just this kind of option that week?".

## Performance

Plays → **Performance** (`src/desk/stocks/play_performance.py`; rebuilt after the close and saved as one view) grades
every play on quoted marks and settlements only: by holding period (1 day, 1 week, 1 month, at expiry — a play that
expired sooner counts its settlement), by category with each rule version separate and earlier versions still shown,
by days to expiry and by price when picked, each beside its random twins; a calibration chart (KESTREL's shown chance
and the market's against how often the event happened, by tenths, with Brier scores); and a cumulative "$100 into
every play" curve, counted at settlement, against the same $100 into each twin and into SPY over the same dates.

## Learning

Code: `src/desk/stocks/play_learning.py`; one report per closed session (`stk_play_learning`), shown under
Performance → What it has learned.

- **Calibration** of KESTREL's chance per category and days to expiry (≤ 7, 8–30, 31–90, over 90), falling back to the
  category and then to every play until a group has 30 resolved plays; only plays of the current probability model
  (`odds.MODEL_VERSION`) count. Each play records which level was applied.
- **Exit rules**: every combination of take profit at +25/50/100/200%, stop at −25/50/75% and leaving after 5/10/21
  sessions (80 rules, holding to expiry first) is tried on each category's quoted closing marks. Chosen
  **walk-forward**: for each block of five sessions, the best rule on plays fully resolved before the block is applied
  to the block's plays and compared with holding them. A rule is **suggested** on the Plays page only when that
  out-of-sample improvement is positive over at least 20 unseen sessions with a Holm-corrected p below 0.05.
- **Inputs**: each session's rank correlation between an input at the pick (KESTREL's chance, its edge over the
  market, expected value per dollar at risk, IV, IV rank, spread, volume ÷ open interest, |delta|, days to expiry, the
  category score) and the return a week later, averaged across sessions with a stationary bootstrap.
- **Challengers** (preregistered, each with its own version hash): liquid contracts only; spreads under 6%; positive
  expected value only; ranked by KESTREL's edge over the market; no results before expiry. Every closed session
  records what each would have picked from the same candidates (`stk_play_shadow`), graded at expiry like the live
  picks. A challenger is **promoted** into a category's live rules (`stk_play_promotions`, a new rule version) only
  after at least 20 later sessions in which it beat the live picks, by a stationary block bootstrap over the session
  series of differences with Holm correction across every challenger tested; after 40 sessions without that it is
  marked **failed** and stays listed.
- **Labels**: with at least 60 settled plays over 20 sessions, a category that significantly beats its random twins
  (and makes money) is **proven** — traded in PAPER where a PAPER strategy already follows that idea, otherwise
  marked eligible; one that significantly trails them or loses money is **failing**. Both appear on the Plays page
  and in Performance; nothing is hidden.

## Verifying Schwab on the laptop

`uv run desk stocks doctor --online` now also asks Schwab's quotes endpoint for three SPY contracts and prints what came
back under `plays_option_quotes` (`state`: `working`, `no_options`, `refused` or `error`, with IV/greeks counts and a
sample). Tracking uses the verified state; until then it tries the endpoint and falls back to chains. The parser
fixtures in the tests are documentation-shaped, not captured broker responses.

## Cost on the laptop

Measured on synthetic data (Ryzen-class cloud CPU): folding a session of 2,000 contracts takes about 2 s once a day;
a marks pass over 3,000 open plays with 20 sessions to mark takes about 1.7 s and 0.1 s when nothing is new. Storage:
about 0.7 KB per contract per session of five-minute marks (kept 35 days), 136 bytes per daily close and 112 bytes per
play mark. With ~100 picks a day plus twins (LEAPS living about a year), expect roughly 0.5–0.7 GB a year plus about
0.2 GB of rolling intraday detail; System → Plays tracking shows the real figure. Requests: at most 40 per five-minute
pass by construction, typically 4–6 a minute.

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
- History values are closing marks at the bid/ask; a real order could fill nearer the mid. Estimated and modelled
  values are Black-Scholes, not quotes, and are never graded.
- Whether Schwab's quotes endpoint serves option symbols to the operator's approved app is unverified until
  `doctor --online` runs on the laptop; Schwab keeps no option price history, so gaps can only be estimated.

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

The restart is needed once: the Stocks page and server move to UI protocol 6 together (History and Performance need
the new endpoints). Saved research, PAPER records and connections are kept; three new research tables arrive by
migration. Plays appear after the next options collection pass (within 15 minutes in market hours); History starts
with the first session recorded after the update.
