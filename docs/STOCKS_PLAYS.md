# Plays — the options of the day

**Plays** (Stocks dashboard → Plays) ranks today's option ideas in twelve categories. Each play is a quoted,
defined-risk position with everything needed to judge it: the action (buy or sell), the type (call, put, straddle
or credit spread), strike, expiration and days left, the stock price, bid/ask and spread, volume and open interest,
implied volatility and IV rank (its year: the collected chains' IV30, else the IV solved from Massive's real closing
trades, and the card says how many days came from each), the greeks (Δ delta, Γ gamma, Θ theta per day, ν vega per volatility point, net for
multi-leg plays), cost or credit, worst case, best case, break-even, expected value, the one-standard-deviation move
by expiry, and three chances of finishing in the money. Research only: no order can be placed from this desk, and
nothing here sells an uncovered option. **Research, not financial advice.**

Code: `src/desk/stocks/plays.py` (categories, unusual activity, recording and grading), `src/desk/stocks/odds.py`
(the probabilities), `src/desk/stocks/static/plays.js` (the page); history, performance and learning are described
below.

## Expiry filter

**Expiry** chooses a band: Up to 2 weeks, About a month, 2 to 4 months, About 6 months, or LEAPS (9+ months), matching
the expiries collection keeps (the nearest weekly, about 30, 60, 90 and 180 days, and the one nearest a year out).
Choosing one does not just hide the rest: the worker has already searched each section's own rules inside every band
(its direction, delta, liquidity and spread rules unchanged; only the days to expiry differ), one per stock, twelve per
band, so "About 6 months" shows that section's best 6-month plays rather than an empty list. Unusual activity, Earnings
plays and Lottos keep their own expiries (the contracts traded, the first expiry after results, short-dated by design);
for them the filter only narrows. Expiry-band plays open like any play and are saved, and followed quote by quote in
History, once opened. Separately, at the closing build each band's picks are recorded as **their own cohort**
(category `<section>@<band>`, under their own rule version) and graded at expiry on the stock's close, which needs no
provider request; they are not tracked intraday or marked daily (so they have no take-profit grade or random twin).
Each keeps only what its grades read (about 1 KB rather than the 4–5 KB of a full play), since a session records
several hundred.
With a band chosen, a section shows that band's own track record, and Performance lists every band's results under
"Expiry filter picks". They never count in a section's usual record.

## Affordable plays and filters

**All prices** is the default, with no automatic price ceiling. Lower-priced candidates appear in visible price-range
rows alongside the overall ranking, including links to their exact contracts. The overall ranking continues to admit
expensive contracts. Optional filters are **Up to $100**, **$100–$250**, **$250–$500**, **Up to $500** or **All prices**.
The earlier automatic ceiling and legacy saved price fields are cleared once by the price-filter preference migration;
subsequent explicit user choices persist. Category, layout and non-price filters remain intact. Dollar amounts mean one whole
contract per leg, including opening fees: total purchase cost for buys and maximum loss for credit spreads. A
$4 option quote is roughly a $400 contract, not a $4 trade. The controls stay labeled when a value is entered,
and **Clear filters** resets eligibility filters while preserving the category and layout.

The worker searches four disjoint price bands through $1,000 **before** selecting one contract per stock and the
top twelve in each category. This replaces the old behavior where a single expensive contract could win for its
stock and the UI only hid it afterward, missing that stock's affordable alternatives. The standard unrestricted
shortlists and immutable closing evaluation cohorts keep their original rules and results. Band-specific nominees
are separate discoveries; custom limits filter the retained band nominees, not every possible market contract.

The budget pass reuses the category's underlying, liquidity, spread, horizon and direction rules. Trend calls,
bearish puts and squeeze calls also have explicit delta ranges so a budget constraint cannot silently substitute
a very remote lotto. ATM straddles use the combined cost of both legs and can choose another qualifying expiry;
credit spreads consider narrower supported pairs within the maximum-loss budget. Price alone is not evidence of
quality, positive expected return or a profitable trading edge. Lottos remain a separately labeled speculative
category, and all probabilities and expected-value estimates remain visible.

Every nominated budget idea receives its own stable identity and first-seen quote archive automatically. It stays
in quote rotation and **History → All identified plays** without requiring a click. Its exact-leg calculator and
personal PAPER confirmation work like other ideas. Budget discoveries do not enter or rewrite the original
closing-cohort calibration and performance record. There is no separate budget track record: each budget idea's own
result is in History → All identified plays, and its Confidence reads its section's recorded plays at its own days to
expiry (see Confidence).

Badges and the result summary show filtered counts, considered candidates and the twelve-play/per-stock display
limits. When filters hide all results, the page says loading is complete, lists exclusion counts, gives the lowest
retained cost when relevant, and offers a reset. Filter failures and a category with no qualifying ideas have
different messages. **Min chance** filters on the card's headline chance, reaching the exit plan's take-profit
before its stop or leave-by date; a card without a plan falls back to its expiry event (and the market's chance when
the KESTREL estimate is absent, including the short option's chance of expiring worthless).

Coverage is limited to collected chains. The existing bounded rotation now interleaves dollar-volume leaders with
stocks priced $5–$100 trading at least $25 million daily (or stricter operator research thresholds), while retaining
requested names, watchlists and core SPY/QQQ handling. This uses the same request budget. Lower stock price is a
coverage heuristic, not an assertion that its options are affordable or liquid; actual option quotes must qualify.
No new provider credentials are required. A saved board from an earlier build is labeled until the worker rebuilds it.

## Personal selections and exact-play details

Click a **BUY/SELL badge** to open an adjacent confirmation window and choose a whole-contract quantity. Confirming
creates a personal selection in **Plays → My tracked plays**, separate from the automatic research cohorts, strategy
PAPER ledgers and uncapped Learning account. It sends **no brokerage order**. Repeated submission of the same
confirmation is idempotent. Ideas, confirmations and lifecycle events are append-only and survive restarts.

Premarket confirmations become eligible at **9:30 AM New York time on the next trading session**, not 9:45. A
confirmation made during the regular session becomes eligible immediately and cannot backdate to that morning's
open. After close, weekends and exchange holidays queue for the next session. The default is the opening session;
**no validated adaptive entry-timing model is active**. The dashboard must be running with collection enabled to
capture an entry. The calendar follows NYSE's early closes (1:00 PM on the day after Thanksgiving, and on Christmas
Eve and July 3 when each is a trading day): on a half day the regular session, and so entries and exits, ends at
1:00 PM.

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

**The calculator workspace** (also opened from any strike in a stock's option chain) follows the popular profit
calculators (OptionStrat, optionsprofitcalculator.com).

- **Stats strip:** net debit or credit, max profit, max loss, break-evens, chance of profit and profit ÷ risk.
- **Strategy templates:** long call or put, the four verticals, straddle, strangle, iron condor, iron butterfly,
  butterfly and calendar. Each is built from the nearest saved strikes, and the strategy is named from the legs.
- **Strike rail:** buy and sell pills against today's price, and leg cards with a strike stepper.
- **Views:** a P/L curve with ±1σ guides, or a price × date table in $ or in % of the amount at risk. A range slider
  sets the price window, and clicking a cell sets the scenario.
- **Controls:** price, date and IV, plus the position's Greeks.

Chance of profit is the options market's own view: a risk-neutral lognormal at the legs' implied volatility. It is
not KESTREL's calibrated chance on the Plays cards and not a forecast. With one expiration the payoff, maximum
profit and loss and break-evens are exact. With **mixed expirations** (a calendar or diagonal) they are given **at
the first expiry, modeled**: legs expiring then at intrinsic value, later legs at Black-Scholes on their implied
volatility, after entry and closing fees, over prices from $0 to three times the highest strike; the chance of
profit is the lognormal to the first expiry at the near legs' volatility, and every number is labelled modeled.

Code: `play_selections.py`, `static/play_workflow.js`; research migration `0010_personal_plays.sql`.

## Profit targets, stock prices and understandable Greeks

Cards show the underlying price next to the ticker, as of the play's quote. Dedicated saved details label their
price at identification. Separate meters show the original event odds, **profit at expiry**, and a **+50% return on
maximum risk at expiry**, with +25%/+100% targets in the expandable explanation. `play_targets.py` integrates the
favorable regions of the exact piecewise terminal payoff under the recorded lognormal forecast, after opening
fees. They do not estimate selling at a peak or hitting a target before expiry. Each recorded play's profit,
+25/50/100% and maximum-gain chances are **graded at expiry** against its real return, and once a group has 30
resolved plays the chance shown is **calibrated** on them, like the in-the-money chance (see "Every chance,
graded"). The meters say "calibrated" when that has happened and "model" before. A saved idea from before targets
existed gets them when its page is opened, **computed later** from its own saved legs, entry and stock price at its
saved KESTREL forecast (else its saved one-standard-deviation move, with no drift), and says so; one that kept
neither (or no stock price) says its targets cannot be computed.

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

## Confidence, exit plans and My bankroll

**Confidence** is five plain checks, and the meter is how many pass (never a disguised probability; the chance of
profit is shown separately). Open it on any card to see each check and why:

1. **Better than even odds**: KESTREL's chance of a profit at expiry is 50% or more (calibrated on graded plays
   once 30 have resolved in its group).
2. **KESTREL's math favours it**: its expected value after fees is above zero.
3. **The research agrees**: bullish plays need a bullish committee lean (≥ 58), bearish ones a bearish lean (< 42),
   condors and butterflies no strong lean (42–58), straddles and strangles a stock moving more than its options price.
4. **Easy to trade**: liquidity A or B (spreads ≤ 12%, open interest ≥ 250).
5. **Proven category**: at least 20 of the category's plays **at the same days to expiry** (≤ 7, 8–30, 31–90 or
   over 90 days, from every recorded cohort of the section, Expiry filter bands included) resolved, averaging a
   profit on the money at risk. A 9-month play is judged by 9-month results, never by the section's 30-to-75-day
   record; with none yet the check says "Not proven yet: 0 of 20 plays resolved at over 90 days to expiry".

5 is Very strong, 4 Strong, 3 Moderate, 2 Weak, 0–1 Very weak. New categories start without check 5.

Every play carries an **exit plan** in the units Learning grades (return on the money at risk, round-trip fees
included): a take-profit, a stop and a date to be out by, each also given as the stock price that would reach it after
a move today and, where time alone gets there, the day it would if the stock stays put. The standard rules are the
common ones per structure: bought options +100% / −50%; long-dated bought options (180+ days) +50% / −30%, out two
months before expiry; lottos +200% and no stop; debit spreads
half the maximum gain / −50%; straddles and strangles +50% / −40%; credit spreads and condors half the credit / a loss
of twice the credit; iron butterflies a quarter of the credit / −50%; cash-secured puts half the premium / a loss of
twice the premium. Bought plays leave 21 days before expiry (a third of the time for short-dated ones). Once a
category's walk-forward exit rule is proven better than holding, that **learned** rule replaces the standard one,
for plays in the days-to-expiry group it was learned on (the group most of its plays were in); a budget or Expiry
filter play at other expiries keeps the standard rule, and its plan says so. On
a play you track, the detail page says where it stands: take-profit reached, stop reached, time to leave, odds
improved (adding is allowed: KESTREL's chance of a profit from here is 10+ points above the same chance at your own
entry, 21+ days remain, and it is not losing more than 25%), or hold. "From here" uses the play's own stored KESTREL
forecast (the volatility and drift behind every chance it showed) over the days left; an idea saved before
forecasts were stored falls back to the latest quoted implied volatility with no drift, and the page says so.

**💼 My bankroll** (the first Plays tab): enter what you have set aside for options and a level, and KESTREL picks the
day's plays from every category, both the overall rankings and the price-range alternatives:

| Level | Worst case per play | All plays together |
|---|---|---|
| Careful | 5% | 20% |
| Balanced | 10% | 35% |
| Bold | 20% | 60% |

Plays need 3 of 5 confidence checks (2 when fewer than three qualify, and the page says so), most confident first,
then best expected value per dollar at risk. One play per stock, two per category, and no direction (bullish,
bearish, neutral, big move) above 60% of the total. Each pick gets as many contracts as its per-play limit allows
within what remains. The summary gives the money at risk, the cash tied up (a sold play's worst case is held as
collateral; a cash-secured put ties up the cash for the shares), what happens if every take-profit or every stop
hits, and KESTREL's expected value. With an amount saved, every other card shows how many of that play fit it. When
nothing fits, the page names the cheapest qualifying play's risk.

## The headline chance: reaching the take-profit

The big number and bar on every card is the **chance to hit the take-profit**: the share of KESTREL's simulated
stock paths (its own forecast drift and volatility, the same ones behind its chance in the money) on which the exit
plan's take-profit is reached before its stop or its leave-by date, every leg repriced along each path at its quoted
volatility (500 paths, checked up to 50 times; a fixed seed per play keeps the number steady, about ±2 points). It is
a path probability, like tastytrade's "P50", because a managed trade succeeds by reaching its target, not by finishing
in the money: a bought option can finish in the money and still lose its premium, and one bought out of the money can
reach +100% weeks before expiry. Under the bar: the chance the **stop** comes first, the chance of **profit at expiry**
(beyond break-even, what Robinhood and OptionStrat headline), and the chance in the money with the market's and
history's views. When today's fair value is already past the stop (a bid/ask spread wider than the stop allows), the
card says so and the chance is 0%. **Min chance (%)** filters on this headline number.

**How it is graded.** Every recorded play keeps its own plan (take-profit, stop, leave-by date) and the chance it
showed. Once its quoted closing marks decide it, the play is graded once (append-only): **take-profit first**,
**stop first** or **neither** by the leave-by date (or expiry). The settlement counts as the last mark; an estimated
or modelled mark before the outcome is decided leaves the play ungraded, because a gap could hide either level. Under
the bar, each card says how often recorded plays shown about the same chance (the same tenth: this category's once it
has 10, otherwise every category's) really reached their take-profit: "History: N plays, X% reached take-profit when
KESTREL said about Y%". Performance charts the graded chance against what happened, with its Brier score. Once 30
plays have resolved, the chance shown is calibrated on them the same way as the in-the-money chance (most specific
group first: this category at this expiry, then the category, then every play); the raw path share is kept beside it
(calibration learns from that), and the stop and neither chances are rescaled to fill the rest.

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

### Every chance, graded

Every chance a recorded play showed is graded once it can be decided, append-only (`stk_play_grades`, research
migration `0014_play_grades.sql`), and calibrated the same way once a group has 30 resolved plays (this category
at this expiry, then the category, then every play), always learning from the model's own number before any
calibration:

| Chance | Graded against |
|---|---|
| Take-profit before the stop or leave-by date | the play's own quoted closing marks and settlement (above) |
| Profit at expiry; +25%, +50%, +100%; maximum gain | the play's graded return at expiry, after its opening fees |
| Touch | the stock's stored daily highs and lows from the session after the pick through expiry |
| In the money (KESTREL, market and history) | the stock's close at expiry |

Performance charts each against what happened, in tenths, with its Brier score; the expiry event shows KESTREL,
the market and the stock's history as three lines. **Confidence**'s odds check reads the calibrated profit chance. Each
build reads only what can still be decided (outcomes from the last 45 days, take-profits of plays expiring within
them; the first run reads everything once), so grading does not slow down as the history grows.

## The categories

| Category | What qualifies |
|---|---|
| 🎯 **Most likely to finish ITM** | Calls where the committee leans bullish (≥ 58), puts where it leans bearish (< 42); out-of-the-money or at-the-money (\|Δ\| 0.25–0.55), 7–60 days, spread ≤ 15%, open interest ≥ 100. Ranked by KESTREL's chance in the money. |
| ⚖️ **Biggest model edge** | Contracts KESTREL values above their ask: expected payoff per dollar paid ≥ +5%. 7–60 days, \|Δ\| 0.15–0.70, spread ≤ 12%. |
| 🐋 **Unusual activity** | See below. |
| 🏷️ **Cheap volatility** | At-the-money straddle (~30 days) where IV30 ≤ 0.9× realized volatility or IV rank ≤ 20. Chance shown: a move past either break-even. |
| 📅 **Earnings plays** | Results date announced in Finnhub's free calendar (read daily when the optional key is set), else a window estimated from past report dates, starting within 21 days; straddle on the first expiry after it; ranked by the stock's median results-day move ÷ the move options price. Each play says which it used. |
| 🎢 **Strangles** | Options not pricing more movement than the stock makes (IV30 ≤ realized, or IV rank ≤ 25): **buy** a call and a put each near 0.30 \|Δ\|, same expiry 21–60 days. Pays on a big move either way for less than a straddle; the chance shown is of finishing past either break-even. |
| 🚀 **Trend calls** | Momentum ≥ 75 and above the 200-day average: call near 0.60 Δ, 30–75 days. |
| 🐻 **Bearish puts** | Valuation ≤ 30, momentum ≤ 35 and below the 200-day, or a red-flag filing: put near 0.45 Δ, 30–75 days. |
| 🔥 **Squeeze calls** | Squeeze score ≥ 60: call near 0.30 Δ, 14–45 days. |
| 🪜 **Debit spreads** | **Buy** a bull call spread where the committee leans bullish (≥ 58), a bear put spread where it leans bearish (< 42): bought leg near 0.55 Δ, sold leg further out near 0.30 Δ, same expiry 21–60 days, paying at most 60% of the width. Less money down than the single contract; the most it can make (the width less the debit) and lose (the debit) are both fixed. Ranked by KESTREL's expected value per dollar at risk; the card shows both strikes and the chance of the maximum gain at expiry. |
| 💰 **Sell rich premium** | IV rank ≥ 60 or IV ≥ 1.3× realized: **sell** a bull put spread (committee ≥ 50) or bear call spread (≤ 45). Short leg near 0.25 Δ, long leg ~5% further out, 21–60 days, credit ≥ 10% of the width. Shows the credit, the worst case and the chance it expires worthless. |
| 🦅 **Iron condors** | Rich premium (IV rank ≥ 50 or IV ≥ 1.2× realized) on a stock without a strong committee lean (42–58): **sell** a put spread below and a call spread above (sold strikes near 0.20 Δ, wings ~5% further out), 21–60 days, credit ≥ 20% of the wider wing. The chance shown is of finishing between the break-evens. |
| 🦋 **Iron butterflies** | The same conditions, sold at the money: **sell** the call and put at the strike nearest the price, buy wings ~5% either side, credit ≥ 40% of the wider wing. The largest credit of the range trades; the full credit needs a finish right at the strike, partial profit anywhere between the break-evens. |
| 💵 **Cash-secured puts** | Committee ≥ 50 or a broad ETF: **sell** a put near 0.25 Δ below the price, 21–45 days, premium ≥ 0.5% of the strike, with the cash to buy 100 shares set aside. The risk shown is that cash less the premium (the stock going to zero). The card shows the effective purchase price and the yield on the cash if the put expires worthless. A covered call at the same strike has the same payoff. |
| 🎰 **Lottos** | Ask $0.05–$0.50, 1–14 days, ≥ 3% out of the money: the most payoff for a one-standard-deviation move, times the chance in the money. Most expire worthless, and the card says so. |
| 🏛️ ~~LEAPS~~ | Retired 2026-11-03 (its past plays stay in History and Performance). Choose **Expiry → LEAPS (9+ months)** instead: every section's own rules searched at that expiry. Collection still keeps the expiry nearest a year out. |
| ⭐ **Your watchlist** | The best-valued contract for each starred stock, in the committee's direction. |

Every structure is **defined risk**: the most it can lose is known when it is shown. Strategies left out, and why:
covered calls (the same payoff as a cash-secured put at that strike, which is offered), protective puts and collars
(they need shares, and Plays holds options only), calendars and diagonals (two expiries: a play's record, its daily
marks and its grade all settle every leg on one expiry's close, and they are not built for a leg that outlives it;
the calculator models calendars at the first expiry), and naked calls, short
straddles, short strangles and ratio spreads (undefined risk; never shown). Two-sided plays (straddles, strangles,
condors, butterflies) are graded as a move past, or a finish between, their two break-evens.

The **Options** page's contract-shortlist menu lists the same strategies: choosing one shows that shape's Plays
picks across categories, ranked by KESTREL's expected value per dollar at risk, with a money-at-risk limit.

Each category shows its **track record** (plays resolved, how often they finished in the money against what KESTREL
predicted, median return at expiry; until one has expired, how the picks stand **so far** at the longest holding
period with quoted closing marks — "So far: 1 week after the pick, median +4% on 12 plays", as Performance counts it)
and, where a PAPER strategy trades the same idea, that strategy's closed-trade record. Filters: calls/puts,
buy/sell, maximum cost, minimum headline chance (to hit the take-profit), expiry, cards or table; the page remembers
them.

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
"Partly opened", "Mostly closing trades or same-day round trips"), and how its price has moved since: from today's
collected chains, or, when the contract is not in them, from its own tracked quotes (a flagged play is recorded and
tracked: today's latest tick, else a later session's closing quote; never an estimate), labelled with the source and
time. That table
sits at the top of the Unusual activity tab. Over time the category's track record shows how often unusual
activity actually finished in the money — a number paid feeds rarely publish.

## The record

A session's plays are **recorded once**, at the first build after that session closes (16:15 New York; 13:15 on a
half day), in
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
**Section and Order.** A Section picker narrows History to one of Today's plays sections (Most likely to finish ITM,
Biggest model edge, … Your watchlist, and LEAPS for the older recorded LEAPS of the retired section); each entry shows
how many plays that session saved in it. With a section chosen, the "This session" summary (average, median, in
profit, $100 in each, random twins) is that section's own, computed exactly as the per-section line below it. Order
lists each section's plays in board order, best or worst latest return (plays without a value yet last), or newest
first (time identified, else first shown on the live board). Both work for All identified plays and Closing snapshot
and every date button, and are saved with the app's display preferences (`stocks-ui.json`, `history`), so a reload
keeps them. The API takes them as `section` and `sort` (`/api/stocks/plays/history`); omitted, the saved choice applies.
History checks saved observations every twenty seconds while visible, preserving scroll and date input focus.
The individual closing-cohort detail page checks every fifteen seconds, updating its chart, exit cards, Greeks and
daily marks without resetting the chart range, basis, quantity or inspected point. **Refresh saved values** retries
immediately. Automatically archived/personal play details use the same freshness indicator with their existing
fifteen-second refresh. A failed page refresh preserves the last values and retries; checking the server does not
claim a fresh provider quote.

On startup and tracking passes, the app automatically recovers up to 500 older first sightings per pass from their
original stored contracts, entry and timestamp. This repairs the earlier two-LEAPS-only failure wherever those
sightings were retained. Missing original ranks and odds remain unavailable; today's model is not used to invent
historical recommendations. A sighting kept no stock price, so its detail page shows the nearest one stored at or
before it, labelled with its source and time: the saved option chain's underlying price that session, else a tracked
quote of one of its legs that session, else the latest daily close already published (never a later price). Unreadable sightings are recorded as skipped rather than
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
intraday retention expires only the stored daily closes remain, each with that day's highest and lowest mid. No historical option quotes are manufactured from
stock prices, and the app does not perform assignment or exercise when displaying an expiry comparison.

### Live tracking (Schwab)

Code: `src/desk/stocks/play_tracking.py`. The dashboard follows every leg of every unexpired recorded play, today's
live board and the twins. In market hours a pass every five minutes quotes what is due from Schwab's `/quotes`
endpoint, 100 option symbols a request (stored OCC symbols are padded to Schwab's six-character root, and each answer
is checked against its strike, call/put, expiry, 100 multiplier and underlying; adjusted contracts are dropped), with
Schwab's own IV and greeks. Contracts it misses fall back to the selected provider's per-expiry chains; anything the
research collector fetched since the last pass is used free. Priorities: the live board, plays from the last five
sessions and anything expiring within a week every pass; older plays every 15 minutes; twins hourly; in the
session's last ten minutes (from 3:50 PM, 12:50 PM on a half day) every contract once more for its close; a closing
row needs a quote from the session's last hour, measured from that day's own close. A pass spends at most 40 requests (under a tenth of the 90-a-minute Schwab budget,
which is shared with research collection). Streaming quotes are not used: the streamer needs Schwab's account-scoped
user preferences, and this integration requests market data only.

During market hours, current quotes are collected and their pass status is saved **before** historical gap repair.
Slow or failed historical downloads cannot block that pass's current quote capture. A quote's provider timestamp,
when available, sets its intraday chart time; yesterday's quote retrieved today cannot become today's quote or close.
Freshness requires usable quotes for every leg, source times within five minutes of one another and no older than
seven minutes. Missing source times are unverified. During trading, a detail page still showing an older value says
**Waiting for today's option quotes** or **Option quotes are stale**, keeps its actual date, and labels it **Last saved
estimate/quote**. It never manufactures a current value from an old estimate. Quote collection remains subject to the
cadence above, provider access and Pause/STOP.

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
Schwab still has minute bars for (its price history serves ten days of them, and gap repair asks for all ten), or
older ones whose minutes were archived when the app fetched them ([intraday](STOCKS_INTRADAY.md)), a five-minute path
is estimated too. Estimates across a results filing,
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
by days to expiry and by price when picked, each beside its random twins; a calibration chart for every graded
chance, by tenths with Brier scores (the expiry event with KESTREL's shown chance, the market's and the stock's
history as three lines; the take-profit chance with how many plays hit take-profit first, stop first or neither;
profit, the return targets, the maximum gain and touch); the Expiry filter's own cohorts by band; and a cumulative
"$100 into every play" curve, counted at settlement, against the same $100 into each twin and into SPY over the
same dates.

## Learning

Code: `src/desk/stocks/play_learning.py`; one report per closed session (`stk_play_learning`), shown under
Performance → What it has learned.

- **Calibration** of KESTREL's chance per category and days to expiry (≤ 7, 8–30, 31–90, over 90), falling back to the
  category and then to every play until a group has 30 resolved plays; only plays of the current probability model
  (`odds.MODEL_VERSION`) count. Each play records which level was applied. The same is done separately for every
  other graded chance (take-profit first, profit, +25/50/100%, maximum gain, touch; `stk_play_grades`).
- **Exit rules**: every combination of take profit at +25/50/100/200%, stop at −25/50/75% and leaving after 5/10/21
  sessions (80 rules, holding to expiry first) is tried on each category's quoted closing marks. Chosen
  **walk-forward**: for each block of five sessions, the best rule on plays fully resolved before the block is applied
  to the block's plays and compared with holding them. A rule is **suggested** on the Plays page only when that
  out-of-sample improvement is positive over at least 20 unseen sessions with a Holm-corrected p below 0.05. It keeps
  the days-to-expiry group most of its plays were in and is applied only to plays in that group.
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

- Coverage is what the collector has fetched: 30 stocks a 15-minute pass from the most-traded names and your
  watchlist (the 150-name focus about every 75 minutes), SPY and QQQ every pass, and the expiries nearest 30, 60, 90,
  180 and 365 days, the nearest weekly and the first after results, up to 50 strikes either side of the money. It is
  not every listed contract.
- Plays are ranked on greeks and implied volatility computed here from the bid/ask mid (Black-Scholes, no
  dividends), the same for every provider. Schwab's own IV and greeks come with its live quotes; tracking stores
  them and the detail page shows them beside KESTREL's.
- Entries in the record use the last quotes of the session (ask to buy, bid to sell). Real fills during the day
  differ.
- Probabilities are model estimates. Until plays have resolved, KESTREL's chance is uncalibrated, and until the
  committee has a positive measured information coefficient it carries no directional tilt. The page states both.
- History values are closing marks at the bid/ask; a real order could fill nearer the mid. Estimated and modelled
  values are Black-Scholes, not quotes, and are never graded.
- Whether Schwab's quotes endpoint serves option symbols to the operator's app is recorded by the first tracking
  pass that tries it (System → Plays tracking shows the state; `doctor --online` checks it on demand), and tracking
  falls back to per-expiry chains when it does not. Schwab keeps no option price history: a gap session uses the
  contract's own Massive daily close where one is stored (its last trade that day, kept as an estimate), otherwise a
  Black-Scholes estimate; neither is graded.

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

Restart after updating: the Stocks page and server move between UI protocols together (protocol 9 now; a page
from an older build asks for a reload). Saved research, PAPER records and connections are kept; new research tables
arrive by numbered migration (the latest for Plays is `0014_play_grades.sql`, the graded chances). Plays appear after
the next options collection pass (within 15 minutes in market hours); grades and calibrations build up as recorded
plays resolve.
