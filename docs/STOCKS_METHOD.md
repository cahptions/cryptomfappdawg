# KESTREL Stocks — methodology (v1)

KESTREL Stocks is a research desk. It reads free public data, scores every liquid US stock with twelve desks, builds
daily Robinhood-style lists with a plain reason for every name, and then keeps score on itself: every list and every
desk is graded against what the market did next. Brokerage adapters supply market data only; no brokerage
order code is present in this desk.
**Research, not financial advice.**

This document is the methodology. Every rule here is frozen by version: the desks, each list and the validation rules
are stored in `stocks-market.db` (`stk_specs`) with a SHA-256 of their content, and a rule changed without a new
version is refused rather than silently mixed into the record (`desk.stocks.daily.register_specs`).

## 1. Nothing from the future

Every number is computed as of a day using only what was public before it:

| Data | Counts on day D only if |
|---|---|
| Financial statements (SEC XBRL: US GAAP, and IFRS for 20-F filers in US dollars) | the filing's `filed` date is before D |
| Trailing twelve months | annual + year-to-date − prior year-to-date, all filed before D |
| Short interest (FINRA) | settlement date + 12 days is before D (FINRA publishes about 8 business days after settlement) |
| Insider trades (Form 4) | filed before D |
| 8-K events, results | filed before D; the price reaction is the session after acceptance (after 16:00 New York means the next session), and only if that session is on or before D |
| Announced results dates (Finnhub's free calendar) | first seen by the decision time and still listed in the newest calendar read by then, or listed up to its report day (a moved or withdrawn date drops out; a new one never shows before it was first seen); confirmed dates and dates estimated from results filings are kept apart (`earnings_dates.known`) |
| Results timing | the 8-K item 2.02 acceptance time in New York: before 9:30 = before the open, before the close = during market hours, else after the close; Finnhub's `hour` when the date was announced; otherwise unknown |
| Past results-day moves (`stk_earnings_moves`) | the report's exit session (the first close that reflects the results) is before D; the priced move is from the last chain observed by 4:15 PM New York on the entry session |
| Treasury yields, VIX | dated before D (Schwab's closing print of $VIX, $TNX or $IRX for a day when connected, else Treasury's par yield or FRED's VIX close) |
| Prices | the close of D and earlier |

Prices are adjusted for splits when read (history is never rewritten). Research-list and desk comparisons are
price-only: dividends are not added on either side. PAPER accounts are paid cash dividends on the ex-date, so they are
compared with SPY's total return, dividends reinvested ([STOCKS_PAPER.md](STOCKS_PAPER.md)). The NYSE calendar is computed by rule (holidays, observed days, Juneteenth from 2022).
`tests/integration/test_stocks_analysis.py` plants a filing and an insider trade after a day and checks that day's
analysis is unchanged; `tests/integration/test_stocks_validation.py` checks that a rebuilt month equals an analysis
run with no prices after that month.

## 2. The universe

Listed US common stock and ADRs from Nasdaq Trader's daily files (no ETFs, funds, warrants, units, rights, preferreds
or test issues), each with its SEC CIK when one exists. On each day a stock is researched only if its close is at
least $1, its 20-day median dollar volume is at least $1,000,000, and it has at least 60 sessions of prices. If the
newest stored prices are more than five days old, nothing is scored rather than score stale prices.

Sectors are the Fama–French 12 industries from the SEC SIC code; banks (SIC 6000–6199), insurers (6300–6411) and
REITs (6798) are scored on the measures that fit them.

## 3. How a desk scores

Each desk turns several measures into percentiles (0–1) across the day's universe, within the stock's sector where
noted (sectors with fewer than 10 names use the whole universe), and blends them by weight into a 0–100 score. Its
**confidence** is the share of the desk's weight that had data for that stock. Verdicts use bands at 75 / 60 / 40 /
25. Every reason shown is generated from the measure that produced it — there is no free text and no language model.

## 4. The twelve desks

| Desk | Measures (weight; ★ = within sector) |
|---|---|
| **Valuation** | EV/EBIT ★ 1.0, P/E ★ 0.7, FCF yield ★ 0.8, EV/sales ★ 0.5, growth the price implies vs actual growth 0.8, its own 2-year history of EV/sales 0.6. Banks and insurers: P/B ★, P/E ★, P/B per unit of ROE ★. REITs: P/FFO ★, dividend yield ★, P/B ★. Fair value: a 10-year free-cash-flow DCF with growth (delivered 5-year revenue growth, capped at 25%) fading to 2.5%, discounted at the 10-year Treasury + 5%; bear and bull cases move growth ±5 points and the discount rate ∓1 point |
| **Quality** | ROIC ★ 1.0, gross margin ★ 0.5, FCF conversion 0.6, accruals 0.6, Piotroski F 0.8, Altman Z 0.4, Beneish M 0.4, net debt / EBITDA 0.5, dilution 0.5 (banks and insurers: ROE ★, net margin ★, dilution) |
| **Growth** | revenue growth 1.0, revenue acceleration 0.8, earnings growth 0.6, operating cash flow growth 0.4, 5-year revenue CAGR 0.5 |
| **Momentum** | 12-month return skipping the last month 1.0, 3-month return 0.5, distance from the 52-week high 0.6, above the 200-day 0.5 and 50-day 0.3 averages, 3-month strength vs its sector 0.6 |
| **Earnings and events** | the abnormal return (vs SPY) on the last results day and the drift since, within 75 days; red flags cap the score at 25: 8-K items 1.03 bankruptcy, 2.04 debt accelerated, 2.06 impairment, 3.01 delisting notice, 4.01 auditor change, 4.02 non-reliance, and forms NT 10-K, NT 10-Q (late filings), 25-NSE (removal from the exchange) and 15-12B (deregistration) |
| **Insiders** | open-market purchases (code P) minus sales outside 10b5-1 plans over 90 days, as basis points of market value; three or more buyers within 30 days is a cluster |
| **Short interest** | short % of shares 1.0, days to cover 0.6, change since the prior report 0.4, short-volume ratio 0.3, fails-to-deliver 0.3 (all lower is better); squeeze score 0–100 = short % (45, full at 30%) + days to cover (25, full at 8) + a rising 21-day return (20, full at +30%) + relative volume above 1.5× (10) |
| **The crowd** | Reddit mention rank and velocity (ApeWisdom); the degen meter (0–5: volatility, short interest, a WSB surge, a call frenzy, a sub-$5 price); hype vs fundamentals. No crowd data for a day means no score |
| **Risk** | volatility 1.0, beta 0.5, the 1-year maximum drawdown 0.6, liquidity 0.4, dilution 0.4 (higher score = lower risk) |
| **Macro and sectors** | the sector's 3-month strength vs SPY; in a risk-off regime (SPY below its 200-day average, high-yield spreads up more than 0.75 points in three months, or VIX above 30) names with beta above 1.3 lose 15 points and below 0.8 gain 10 |
| **Options** | (v4, 2026-11-18) what option prices say about the stock: the call-put IV spread +, put skew −, option-to-stock volume (log) −, the week's change in call IV + and in put IV −, the borrow rate −, each z-scored across the day's stocks with option signals (section 11); score = 100 × the normal curve of the mean signed z; an observer with no vote until its own live record is graded. IV rank and the cheap-or-expensive measures stay on the page as facts. IV rank's year: the collected IV30 where it exists, else the IV solved from Massive's real closing trades (`desk.stocks.iv_history`) |
| **News and world** | the week's company-news tone (Massive's sentiment, positive minus negative), attention (articles in three days against the usual daily count), supply shocks from world events and trade actions that touch the company; an observer with no vote until graded ([STOCKS_NEWS.md](STOCKS_NEWS.md)) |

## 5. The committee

The committee blends the desks: each desk's score × its weight × its confidence (floored at 0.25). Default weights:
valuation 1.0, quality 1.0, growth 0.8, momentum 1.0, earnings 0.6, insiders 0.6, shorts 0.4, risk 0.6, macro 0.4,
crowd 0, options 0 and news 0. Labels: Bullish ≥ 70, Bullish lean ≥ 58, Neutral ≥ 42, Bearish lean ≥ 30, else Bearish. A desk
30 or more points from the committee is listed as dissent.

Crowd, options and news start at zero weight because there is no history to prove them. Weights change only through the
validation in section 8.

## 6. The lists

Each list holds up to 25 names, each with its reason. Lists marked *tested* make a claim the report card tests;
*describe* lists are tracked for what happened next but make no claim. *Live only* lists need crowd or options data,
which exist only from the first day of collection.

| List | Rule | Claim |
|---|---|---|
| Trending today (v2) | relative volume 45% (full at 5×), absolute daily move / daily volatility 35% (full at 4σ), qualified same-session sampled options premium percentile 20%; reweight available components; require volume ≥ 1.5× or a ≥ 2σ move; no social or committee input | describe, live only |
| WallStreetBets | covered tickers ranked by the source's rolling-24h WSB mentions; ties use source rank then ticker; counts, change, reported upvotes and observation time shown separately | describe, live only |
| Top gainers / losers | 1-day return | describe |
| Undervalued | valuation ≥ 70, quality ≥ 40, no red flag, valuation confidence ≥ 0.5 | outperform |
| Overvalued | valuation ≤ 25 with confidence ≥ 0.5 | underperform |
| Quality at a fair price | quality ≥ 70, valuation ≥ 45, no red flag | outperform |
| Momentum leaders | momentum ≥ 75 and above the 200-day average | outperform |
| Earnings this week | next report window (from the gaps between past results) centred within 7 days | describe |
| Just reported | results within 8 days, by the size of the reaction | describe |
| Insider buying | open-market buys > non-plan sales in 90 days; clusters first | outperform |
| Squeeze watch | squeeze score ≥ 50 | describe |
| Unusual options activity | volume ≥ 500 and ≥ 3× usual, by premium | describe, live only |
| Cheapest IV | lowest IV30 / 20-day realized | options claim (below), live only |
| Biggest expected moves this week | first expiry within 10 days, by straddle / price | describe, live only |
| Value traps to avoid | valuation ≥ 70 with quality ≤ 35, growth ≤ 25 or a red flag | underperform |
| Dilution watch | diluted shares +5% in a year, or an S-1, S-3 or 424B prospectus filed in 90 days | underperform |
| WSB vs reality | crowd ≥ 50 and \|crowd − fundamentals\| ≥ 25 | describe, live only |
| New 52-week highs / lows | close within 0.3% of the 52-week high / low | describe |
| The committee's picks | highest committee score with ≥ 6 desks counted | outperform |

Trending measures activity rather than a direction or expected profit. Its daily volatility is the annualized
20-session volatility divided by √252. The optional options component uses the percentile of sum(volume × midpoint ×
100) among covered, quote-qualified same-session names; it is a premium proxy, not exchange trade flow or an assertion
of buying. Missing options do not block an otherwise qualifying stock. WSB has no input to this ranking. The WSB tab
shows reported upvotes rather than inferring sentiment or independent users from them. Its source's observation time
and rolling window remain attached to the recorded daily row.

## 7. The track record

After each session closes (75 minutes after, by default), once that session's prices are stored, the day is analysed
and **every list is appended to `stocks-research.db` once** — including lists that were empty — together with every
stock's compact desk scores. The tables are append-only (SQLite triggers refuse updates and deletes); a later refresh
the same day updates the stock pages but never the record. A list labelled with a day is never built from an earlier
day's prices.

## 8. Proving it (`desk.stocks.validation`)

**Two records, never mixed.** The *live* record is the daily lists above. The *rebuilt* record runs the same code as
of each past month's last session (with at least a year of prices before it), on only what was public that day, and
keeps the result in `stk_rebuild_months` keyed by the methodology's hash. Each rebuilt month's universe is the
listings as they stood that day (from when a listing first appeared in the files to the last file that had it), so
companies delisted since collection began are included. Companies delisted earlier but within Massive's two years come
from Massive's reference list of inactive common stocks and ADRs (`desk.stocks.delisted`, once): their grouped-daily
bars are fetched again and kept up to the delisting day, their filings and facts are fetched once, and they count
until their delisting day, so from the first Massive day the rebuilt and replayed universe is survivorship-free (a
ticker listed again today by another company is left out: the two can't be told apart). Schwab's earlier years
(section 9) hold only companies still listed when the Massive window began: there is no free price source for
companies delisted before it, and this remaining survivorship bias flatters lists that buy and hurts lists that avoid.
The report therefore gives every list's and desk's rebuilt record for the two periods separately
(`periods`: survivorship-free from that day, and the earlier survivors-only years), the replay gives each account's
return for both, and `desk stocks report` prints them. The live record has no such bias.

**Forward returns.** A list is bought at the next session's open and held 5, 21, 63 or 252 sessions (1 week, 1
month, 3 months, 12 months), sold at that session's close; a stock that stops trading is sold at its last close. Each
observation's return is the equal-weight mean of its picks, compared with SPY and with each pick's sector fund (XLK,
XLF, XLE, XLV, XLI, XLY, XLP, XLU, XLB, XLC) over the same sessions.

**Claims.** An *outperform* list is tested on its return minus SPY minus a 0.2% round trip (spread and slippage on a
commission-free trade). An *underperform* list is tested on SPY minus its return (you avoid it; no cost). The hit
rate is the share of picks that did what the list claims. The primary horizon is one month.

**Desks.** Each observation's information coefficient (IC) is the Spearman rank correlation between the desk's score
and the next month's return across the universe (at least 10 names). Also reported: the return spread between the top
and bottom tenth (fifth below 50 names) and the share of observations where the top tenth beat SPY after costs. The
risk desk claims to predict *drawdowns*, so it is tested on the spread in the next quarter's worst drawdown between
its top and bottom tenth. Live desk scores are judged weekly; rebuilt ones monthly.

**Statistics.** Each claim's series gets a 95% stationary-bootstrap interval for its mean and a one-sided p-value
(2,000 resamples, seed 0, block length chosen from the series' autocorrelation, so overlapping daily lists aren't
counted as independent). All tested claims in a record (lists, desks, the committee and the cheap-IV claim) are
Holm-adjusted together. *Independent periods* count non-overlapping horizons: 252 daily one-month observations are
about 12 independent months.

**Grades.** With fewer than 12 independent months: *too early*. Otherwise *working* (Holm-adjusted p < 0.05),
*promising* (p < 0.10, not yet surviving Holm), *backwards* (p > 0.95: it did the opposite) or *not working*. The
headline grade uses the live record once it has 12 independent months, else the rebuilt one (labelled as such).

**Learned committee weights.** For each rebuilt month, weights are fitted only on months whose one-month returns were
complete before it: each desk's weight is proportional to its average positive IC, shrunk toward the defaults by
n / (n + 12) for n months of evidence. The learned weights replace the defaults only if, over at least 6 months they
never saw, they ranked stocks better (higher mean IC) than the defaults. Crowd and options keep weight 0 until their
live IC is graded *working*, when they get 0.4. The weights in use are saved with each report
(`stk_validation.committee_weights`) and picked up by the next analysis.

**Options truth.** For every stored option summary whose future has arrived: did the next month's realized volatility
come in below IV30 (options were rich), and did the stock move more than the first expiry's at-the-money straddle?
Per symbol too, for the options page. The cheap-IV list claims its picks realize more of their implied volatility
than other optioned stocks on the same day; that claim is tested like the others.

Each report is appended to `stk_validation` (append-only) with the hash of the rules it was built under.

**Weekly evaluation.** The Evidence page's evaluation (rebuilt months, list and desk grades, proposed committee
weights) also runs by itself once a week while collection is on (`EvidenceWorker.weekly`: due seven days after the
last finished one, never beside a running replay, not again within six hours of a failure), so the page always has a
recent result and says when it last ran and is next due. It never adopts anything: the proposed weights are shown
beside "Not adopted", and adopting them stays the user's explicit choice (`desk stocks validate`).

## 9. Known limits

- Massive's free plan serves two years of daily bars. When Schwab is connected, its price history adds earlier years
  (`desk.stocks.schwab_history`, `price_history_years`, default 10, at most 20): one request per stock, benchmarks
  first and then the research universe by dollar volume, at most 40 stocks a collection pass through the shared
  Schwab limiter. Rows go only on days before the first stored Massive day, so a Massive row is never overwritten,
  and those days are labelled `schwab_pricehistory` in `stk_bar_days`. Whether Schwab's candles are split-adjusted
  is checked per stock, never assumed: over the shared sessions its closes must match Massive's unadjusted closes
  either as they are or divided by the stored splits since each day, with volumes agreeing on the same basis; when no
  split falls in the overlap, each earlier split of at least 3-for-2 must show the basis in Schwab's own candles (an
  overnight gap of the ratio, or none). Adjusted candles are un-adjusted with the split record (Massive's splits and
  dividends, back-filled a month a pass, `corporate_history`); candles older than that record are not stored. A stock
  that doesn't reconcile is refused and its note kept in `stk_history_symbols` (Settings and the replay's coverage
  line count them). The earlier years enter the evidence check, rebuild and replay only once every queued stock has
  been checked, so a half-filled year never counts. Company facts are kept for 16 years so rebuilt months in those
  years have their fundamentals (XBRL is complete for US filers from 2011).
- The bound, measured on synthetic data here (the Dell's and a cloud server's speed may differ): a stored bar takes
  about 76 bytes on disk with its index, so ten years of the research universe (about 4,500 stocks, fewer listed in
  the early years) is roughly 9 million bars, about 650 MB (twenty years: about 1 GB). Loading takes about 3.4 µs and
  at most about 100 bytes of memory per bar (an evaluation that wouldn't fit beside the 1.5 GB memory floor starts
  later and says so). A rebuilt month or replayed session scores every liquid stock, about 5 ms each, so about 15 s
  for 3,000 stocks: the first rebuild of ten years (about 120 months) takes about half an hour, and later rebuilds
  only add new months. Day features and replayed sessions read only their own lookback (a year of prices; 600
  sessions for a replayed day), so more stored years cost nothing per session. A replay covers at most the last
  1,260 sessions (five years, about 5 hours), so it finishes between two closes and its report stays under 4 MB;
  older years are graded by the monthly rebuild. Ten years by default because before 2011 there are no XBRL
  statements, and five-year growth needs five of them: earlier years add storage without adding the company desks.
  On a small server, lower `price_history_years` (0 turns it off).
- Rebuilt months before the first Massive day carry survivorship bias (section 8); from it on they don't, once the
  list of delisted companies is in. The live record has none.
- Research-list and desk returns are price only; dividends are ignored on both sides (PAPER includes them on both).
- Short interest is twice monthly and published with a lag; insider data depends on filers' accuracy.
- Foreign private issuers filing 20-F under IFRS are read from the `ifrs-full` taxonomy, the same way as US GAAP
  (annual reports only, so their trailing twelve months is the last fiscal year). Only US-dollar values are read: an
  IFRS filer reporting in another currency has no fundamentals, because nothing converts currencies. An ADR's price is
  per depositary share while the filed share count is ordinary shares, so its per-share valuation is off by the ADR
  ratio when that isn't one (as for any foreign filer).
- Crowd counts are a snapshot of Reddit attention from one aggregator, not sentiment.
- Option chains are collected for a ranked focus list (`options_focus_symbols`: dollar-volume leaders interleaved with
  liquid lower-priced stocks, plus held contracts): 30 stocks every 15 minutes during market hours (SPY and QQQ every
  pass), one Schwab chain request per stock covering all the expiries it needs, so the 150-name focus is covered about
  every 75 minutes. A name's chain is a snapshot from some time in the session rather than a continuous intraday
  history.

## 10. Volatility forecast (`desk.stocks.vol_forecast`, 2026-11-18)

*How much KESTREL expects the stock to move, learned from its recent daily, weekly and monthly swings plus what options
imply.* It sets the spread of every KESTREL chance on Plays (and the lotto replay's ranking). Until 2026-11-18 that was
a fixed blend, 0.7 × implied variance + 0.3 × realized variance; research on the HAR model (Corsi 2009) and its
implied-volatility extensions shows that regressing the variance that follows on recent daily, weekly and monthly
realized variance plus implied variance forecasts better than either alone. The blend stays as the fallback, and a
horizon switches to the learned forecast only after it beat the blend out of sample.

1. **Realized variance**, once per stock and finished session (`stk_rv_daily`, market migration 0014), for the stocks
   with collected chains or an IV history (at most 500, by dollar volume; the latest 1,000 sessions). With archived
   minute bars covering at least 75% of the session (and the last minute's close within 2% of the day's close):
   the sum of squared five-minute log returns from the first open, plus the squared overnight return (previous close
   to that open): source `minutes5`. Otherwise the squared close-to-close log return: source `daily`. Prices are
   split-adjusted; a newly recorded split recomputes that stock's rows. A `daily` row from the last 10 sessions is
   recomputed if its minute bars are archived later. Each row says which source it used.
2. **Implied variance**: the stock's 30-day at-the-money implied volatility that day — the collected chain's IV30, else
   the one solved from Massive's real closing trades (`iv_history`) — as IV² / 252 per session. A day without one has
   no HAR-IV row; that stock-day still feeds the HAR-RV model (the same regression without implied variance).
3. **The model.** Pooled across stocks, ordinary least squares in log variance, one regression per horizon h of 1, 5,
   10, 21 and 63 sessions. Target: the log of the average realized variance over the next h sessions. Features: an
   intercept, the log of the day's realized variance, of its 5-session and of its 22-session average, and (HAR-IV) the
   log of the implied variance. Each day's variance is floored at 10⁻⁶ (a 0.1% move) before the log, so a flat close
   is not an infinitely calm day. The log forecast is turned back into a variance with Duan's smearing factor (the
   mean of exp(residual) over the last 252 sessions of training rows), so it is not biased low. Between fitted
   horizons the log variance is interpolated linearly in h; beyond 63 sessions the forecast reverts to the stock's own
   252-session average variance: v(h) = (63·v(63) + (h − 63)·long-run) / h.
4. **Walk-forward, no look-ahead.** A fit is made on the first session of each week, from the rows whose whole target
   window ended before that session (the h-session purge), expanding from the first stored session; at least 1,000
   stock-sessions. A forecast made on day D uses the newest fit on or before D. Fits are appended to `stk_vol_fits`
   with their fit day and a hash and never rewritten. A test plants a shock after a day and checks that no fit or gate
   on or before it changes.
5. **Honest evaluation and the gate.** Every forecast is scored against what followed, out of sample: QLIKE
   (y/f − ln(y/f) − 1, the standard robust loss for variance forecasts) and the squared error of log variance, for
   HAR-IV, HAR-RV, implied volatility alone and the old blend, on the same stock-days. Each fit stores the scores of
   the forecasts realized before its fit day. **A horizon is live only when those held-out forecasts cover at least
   60 sessions and 30 stocks and HAR-IV has both a lower QLIKE and a lower log-variance error than the old blend**;
   otherwise that horizon (and the horizons interpolated next to it) keeps the old blend. Without an implied
   volatility (rare: Plays always has one), HAR-RV is used only where it beat realized volatility alone the same way.
6. **Use.** `vol_forecast.forecast_sigma(iv, rv, t=…, vol=…)` returns the annual volatility for a horizon of t years
   (h = t × 252 sessions) and the method used (`har_iv`, `har_rv` or `blend`). Plays feeds each contract's own implied
   volatility into the IV term, so the smile's shape is kept as under the blend. A replay on a past day (Plays'
   replay, the lotto replay) uses the newest fit on or before that day and the stock's realized variance up to that
   day's close, else the blend.
7. **Cost.** The realized-variance update runs in each collection pass with a 60-second budget (the rest next pass);
   the weekly refit rebuilds the walk-forward from the stored rows once every stock is current (a fraction of a second
   per 10,000 stock-sessions; about 10 seconds for 500 stocks × 1,000 sessions). `desk stocks vol-forecast`
   rebuilds and evaluates on demand, with progress.

Limits: most stored sessions before the minute archive began use the noisier close-to-close measure, which mainly
lowers how much weight the regression can give the single day. The evaluation scores every horizon with the 30-day
IV, whereas Plays uses each expiry's own IV; both the learned forecast and the blend are scored on the same IV, so the
comparison is fair, but its size at very short and very long expiries is an estimate.

## 11. Options desk: what option prices say about the stock (`desk.stocks.option_signals`, 2026-11-18)

*Option traders often know something first; these are the option-price patterns that have predicted stock returns in
published research.* Each is measured per stock and session from one chain (spec `stocks_option_signals`, table
`stk_option_signals`, market migration 0015), and missing — with the reason in the row's snapshot — when the chain does
not have enough usable quotes. A quote counts only with a bid above zero, an ask at or above it, (ask − bid) / mid at
most 50%, a mid of at least $0.05 and a solvable Black-Scholes IV (the day's 3-month Treasury yield, the stock's
trailing dividend yield); a stock-session counts only with at least 10 such contracts.

| Signal | Measure | Research | Direction |
|---|---|---|---|
| Calls vs puts (`iv_spread`) | open-interest-weighted average of call IV − put IV over call-put pairs at the same strike and expiry, 10–60 days out, within 30% of the price (at least 3 pairs) | Cremers & Weinbaum 2010 | calls richer → higher returns |
| Price of crash protection (`skew`) | IV of the put with \|delta\| nearest 0.225 (0.15–0.35; else the strike 4–12% below the price nearest 7.5%) minus the at-the-money call's IV, in the expiry nearest 30 days (10–60) | Xing, Zhang & Zhao 2010 | steeper → lower returns |
| Option vs share trading (`os_ratio`) | contracts traded (expiries within 120 days) × 100 / shares traded that session; and its log against the stock's own median of the previous 60 stored sessions (at least 20) | Johnson & So 2012 | heavier → lower returns |
| Call / put IV, past week (`d_call_iv`, `d_put_iv`) | change in the 30-day at-the-money call (put) IV against the row 4–8 sessions earlier nearest 5 | An, Ang, Bali & Cakici 2014 | call IV up → higher; put IV up → lower |
| 3-month vs 1-month IV (`term_slope`) | 90-day minus 30-day at-the-money IV (constant maturity, total-variance interpolation; nearest expiry within 12 / 30 days otherwise) | Vasquez 2017 | predicts option (straddle) returns, not the stock: Learner and stock page only |
| Options vs actual moves (`iv_rv`) | 30-day IV over realized volatility of the last 22 sessions (`vol_forecast`'s realized variance) | Vasquez 2017 | as above |
| Cost to borrow (`borrow`) | Schwab's `htbRate` as sent (its unit is unverified; the desk uses only its rank across stocks) when a quote sends it; 0 when Schwab says the stock is easy to borrow (`isHardToBorrow` false, no rate); otherwise missing. Recorded from the chain scan's quotes going forward only | borrow-fee studies (much of the skew and IV-spread signal is short-sale cost) | dearer → lower returns |

**Point in time.** A row is computed from one session's chain and that session's share volume. Measures that need
other sessions (the volume ratio's median, the IV changes) are derived when read, from that row and earlier rows only;
a test writes wild values after a day and proves the day's signals do not change. Rows come from the after-close chain
scan (`desk.stocks.chain_scan`, [STOCKS_SOURCES.md](STOCKS_SOURCES.md)) or, for stocks and days the scan did not
cover, the chain collected during the session; each row says which. Collected chains already stored are back-filled
newest first, a minute a collection pass (job `option_signals`), each finished session once.

**The Options desk (desks v4).** Each day, each of the six desk signals (call-put IV spread +, skew −, the log of
option-to-stock volume −, the change in call IV +, the change in put IV −, the borrow rate −) is turned into a
z-score across the stocks in the day's universe that have it (at least 10; capped at ±3), multiplied by its research
sign, and the stock's score is 100 × Φ(mean of its signed z-scores): 50 is the day's average, about 84 one standard
deviation better on every signal. Confidence is the share of the six signals the stock has; reasons name each signal
and its study. Rows older than three sessions or with fewer than 10 liquid contracts do not count.

**It earns its vote.** The desk starts at weight 0 (an observer) like the other live-only desks. Its live record
(`desk.stocks.validation`, v3) is its daily score's rank correlation with the next month's return, sampled weekly,
counting only sessions recorded after desks v4 was first registered (the earlier "Options" scores measured how cheap
options were, not this). Only when that record has 12 independent months and survives the Holm correction with the
other claims (graded *working*) does the committee give it 0.4, the same rule as Crowd and News. Until then Evidence
shows its record and, separately, each signal's own forward record from the stored signals (`option_signal_record`:
the research-signed signal ranked against the next month's return on every fifth session; the term slope and IV over
realized volatility are shown too but are not in the desk). Those per-signal records are shown, not tested with the
claims: they guide, they never vote.

**Where to see it.** The stock page's **Options signals** panel shows each signal in a plain sentence, a one-line hint
of what it has meant historically (coloured only when the research gives it a direction), and where the stock ranks
among the stocks measured on the newest session with signals; a missing signal is marked *Not measured* with the
reason. Evidence shows the Options desk's status (observer or voting), its live record and each signal's own forward
record; Evidence → Data health shows the chain scan's coverage.

## 12. Direction and volatility (`desk.stocks.forecast`, 2026-11-18)

*Where will the stock go, and how much will it move?* An option's outcome mixes the two. Bali, Beckmeyer, Moerke &
Weigert (2021) find delta-hedged option returns more predictable than stock returns, by nonlinear models, with
option-based characteristics (IV level and shape, moneyness) the strongest; Goyal & Saretto (2009) find the gap between
realized and implied volatility predicts delta-hedged straddle returns. So KESTREL learns the two apart, each on far
more data than its own plays could give, and uses each only where it beat a no-skill baseline out of sample.

**Direction model** (`forecast/direction.py`, spec `stocks_direction_model` v1). Quantiles (5, 10, 25, 50, 75, 90, 95%)
of each stock's log return from the decision close over 1, 5, 10, 21 and 63 sessions, **standardized** by the stock's
reference scale (root mean square daily return over the previous 63 sessions × √h), so the trees learn shape and tilt
rather than each stock's volatility. Inputs (`forecast/panel.py`), all as of the previous close: 1-week and 1-month
returns (short-term reversal), 12-1 momentum, nearness to the 52-week high, realized variance over a day, a week and a
month and its ratio to the reference, dollar volume and its 5- vs 63-session surge, SPY's 1-week and 1-month returns
and realized variance, the sector fund's 1-month return and the stock's return against it, the committee score (from
the daily score history, where it exists), three option signals (call−put IV spread, OTM put skew, IV ÷ realized
volatility; section 11; newest within five sessions) and days to the next results (an announced date first seen by
then, else the last results filing + 91 days); missing stays missing. Data: the 1,200 stocks with the most dollar
volume over their whole stored history (at least 300 sessions; no ETFs), every fifth session per stock (offset by
stock), rows only where the stock traded at least $2 million a day over the previous 20 sessions; split-adjusted
closes from the saved daily bars (the ten Schwab years and Massive's two). Model: scikit-learn's histogram gradient
boosting with quantile loss, one model per horizon and quantile (80 trees of 15 leaves, at least 200 rows a leaf), at
most 150,000 rows per final fit and 80,000 per evaluation fit, one thread; the predicted quantiles of a row are sorted.

**Evaluation and gate.** A fit on the first session of each week trains on rows whose outcome was known before it.
Its evaluation predicts each of the three 252-session blocks before the fit day with models trained only on rows
whose outcome was known before that block began (purged), against the **baseline**: the same rows' unconditional
quantiles (no skill; KESTREL's behaviour before: drift-neutral). Scores: pinball loss (the proper scoring rule for
quantiles), calibration (how often the realized return fell below each predicted quantile; error = mean distance from
its target), and the **market-neutral** cross-sectional rank correlation of the predicted median with the realized
return within each session (a within-session correlation ignores the market's common move). A horizon goes live only
with ≥ 5,000 held-out rows over ≥ 60 sessions and ≥ 30 stocks, a lower pinball loss with a Newey–West t ≥ 2 across
sessions, and a calibration error no worse than the baseline's + 0.01. A synthetic universe of pure noise never goes
live; a planted one-week reversal does at the short horizons (tests).

**Volatility-surprise model** (`forecast/vol_surprise.py`, spec `stocks_vol_surprise` v1). Target: log(mean
realized variance over the next h sessions ÷ IV30² / 252) per stock, from section 10's realized variance. Baseline:
HAR-IV's own log forecast of that ratio from the fit stored for that day (section 10). Inputs: the implied level, the
IV / 22-session realized gap, the HAR inputs and HAR-IV's forecast, the term slope, OTM put skew, call−put spread, days
to the next results and whether they fall inside the window. Gradient-boosted trees (squared error), every third
session per stock, four 63-session purged blocks; live with ≥ 2,000 held-out rows over ≥ 60 sessions and 30 stocks
and a lower squared error than HAR-IV alone (Newey–West t ≥ 2). Used as a multiplier e^{(prediction − HAR)/2} on
KESTREL's volatility (bounded 0.5–2), linear between horizons, fading as 63/h beyond 63 sessions; 1 where not live.

**Weekly fit.** Both fit in the Learner's niced training subprocess (once per week's fit day, before the Learner
trains; a week without enough data is tried once) or with `desk stocks forecast-fit`; fits are append-only
(`stk_forecast_fits`, market migration 0017) with their evaluation, rows, time and peak memory; the newest eight fits'
models are kept (`stk_forecast_blobs`, hash-checked); a day whose fit is no longer kept uses the baseline, never a
later fit. `desk stocks forecast-bench` times one direction fit on a synthetic universe of the default size.

**Use.** Plays values every structure from the two (docs/STOCKS_PLAYS.md "What a play is worth"); the Learner reads
them as features (docs/STOCKS_LEARNER.md); the Learning report and the Learner page show which horizons are live.

