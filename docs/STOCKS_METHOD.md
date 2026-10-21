# KESTREL Stocks — methodology (v1)

KESTREL Stocks is a research desk. It reads free public data, scores every liquid US stock with eleven desks, builds
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
| Financial statements (SEC XBRL) | the filing's `filed` date is before D |
| Trailing twelve months | annual + year-to-date − prior year-to-date, all filed before D |
| Short interest (FINRA) | settlement date + 12 days is before D (FINRA publishes about 8 business days after settlement) |
| Insider trades (Form 4) | filed before D |
| 8-K events, results | filed before D; the price reaction is the session after acceptance (after 16:00 New York means the next session), and only if that session is on or before D |
| Treasury yields | dated before D |
| Prices | the close of D and earlier |

Prices are adjusted for splits when read (history is never rewritten) and are price-only: dividends are not added on
either side of any comparison. The NYSE calendar is computed by rule (holidays, observed days, Juneteenth from 2022).
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

## 4. The eleven desks

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
| **Options** | IV30 / 20-day realized volatility 1.0, IV rank 0.8, term structure 0.3, put/call volume 0.3 (for the most-traded names whose chains are collected) |

## 5. The committee

The committee blends the desks: each desk's score × its weight × its confidence (floored at 0.25). Default weights:
valuation 1.0, quality 1.0, growth 0.8, momentum 1.0, earnings 0.6, insiders 0.6, shorts 0.4, risk 0.6, macro 0.4,
crowd 0 and options 0. Labels: Bullish ≥ 70, Bullish lean ≥ 58, Neutral ≥ 42, Bearish lean ≥ 30, else Bearish. A desk
30 or more points from the committee is listed as dissent.

Crowd and options start at zero weight because there is no history to prove them. Weights change only through the
validation in section 8.

## 6. The lists

Each list holds up to 25 names, each with its reason. Lists marked *tested* make a claim the report card tests;
*describe* lists are tracked for what happened next but make no claim. *Live only* lists need crowd or options data,
which exist only from the first day of collection.

| List | Rule | Claim |
|---|---|---|
| Trending today | crowd attention 50%, volume vs normal 30%, today's move 20%; crowd ≥ 30 or volume ≥ 2.5× | describe, live only |
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

## 7. The track record

After each session closes (75 minutes after, by default), once that session's prices are stored, the day is analysed
and **every list is appended to `stocks-research.db` once** — including lists that were empty — together with every
stock's compact desk scores. The tables are append-only (SQLite triggers refuse updates and deletes); a later refresh
the same day updates the stock pages but never the record. A list labelled with a day is never built from an earlier
day's prices.

## 8. Proving it (`desk.stocks.validation`)

**Two records, never mixed.** The *live* record is the daily lists above. The *rebuilt* record runs the same code as
of each past month's last session (with at least a year of prices before it), on only what was public that day, and
keeps the result in `stk_rebuild_months` keyed by the methodology's hash. The rebuild starts from today's listings,
so companies delisted before collection began are missing: this survivorship bias flatters lists that buy and hurts
lists that avoid, and the report says so. The live record has no such bias.

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

## 9. Known limits

- The free price source keeps two years of daily bars, so the rebuild covers about a year of months at first and
  every grade starts as *too early*. A longer price history (Schwab, when connected) lengthens the rebuild.
- Rebuilt months carry survivorship bias (section 8). The live record does not.
- Returns are price only; dividends are ignored on both sides.
- Short interest is twice monthly and published with a lag; insider data depends on filers' accuracy.
- Crowd counts are a snapshot of Reddit attention from one aggregator, not sentiment.
- Option chains are collected for the most-traded names only, once a day after the close (no intraday history).
