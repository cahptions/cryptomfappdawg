# Stocks and options product review — 2026-10-24

The objective is a complete research workflow: discover an idea, inspect its evidence and age, compare executable
structures, understand risk, test the hypothesis chronologically, and follow its subsequent results. Attractive charts
alone do not accomplish that. KESTREL is not yet demonstrated to outperform established research products in market
coverage, signal quality, execution realism, or usability. This review separates shipped repairs from remaining work.

## Scope and evidence

Reviewed the stock/ETF profile, chart controls, discovery and options lists, contract screener, payoff calculator,
forward PAPER, historical replay, Learning, Evidence, source setup, worker ownership and recovery paths. Source inspection
included `web.py`, `backtest.py`, `backtest_job.py`, `replay_archive.py`, `paper/*`, `learning*`, `explore.py`,
`options_view.py`, `activity.py`, `stock_views.py`, `calculator.py` and their browser components and tests.

The reported screenshot has three daily observations, ten open positions and no closed trades. It is a monthly-rebalanced
momentum portfolio, not an intraday execution strategy. A line between three closes does not describe the intervening price
path. On a closed-market weekend there is no new daily session to process. The interface must explain that while offering
historical simulation immediately; neither waiting for a grade nor restarting the application creates missing history.

Public competitor pages were retrieved successfully on the review date. They advertise these useful workflow patterns:

| Product and official reference | Relevant pattern | Requirement for KESTREL |
|---|---|---|
| [TradingView](https://www.tradingview.com/features/) | Integrated charting, screeners, alerts, fundamentals and options analysis | Consistent charts and saved screens; timely, attributable alerts rather than isolated data displays |
| [OptionStrat](https://optionstrat.com/) | Strategy construction, idea optimization, unusual trades | Carry a specific observed contract directly into payoff/scenario analysis; compare structures under the same assumptions |
| [Unusual Whales](https://unusualwhales.com/) | Options flow, market-maker exposure and other discovery feeds | Distinguish actual transaction evidence from quote/volume proxies; explain every ranking's inputs and coverage |
| [thinkorswim](https://www.schwab.com/trading/thinkorswim) | Integrated analysis, multiple clients and paperMoney | A coherent inspect/simulate/review workflow, with clear account states and actionable diagnostics |
| [Stock Analysis screener](https://stockanalysis.com/stocks/screener/) | Broad, filterable company research | Comparable, exportable filters and transparent universe coverage |

These are observations of public product descriptions, not paid-product trials, performance comparisons, licence grants,
or evidence that KESTREL has equivalent data. Do not copy branding, proprietary feeds or inaccessible analytics.

## Repairs and additions in this change

| Finding | Change |
|---|---|
| Forward charts could stop polling entirely when no historical job ran | PAPER now refreshes both its forward record and replay state automatically; errors retry. Other background refreshes no longer replace an active PAPER workspace |
| 1M/3M/Max could be identical with only three observations | Ranges are generated from available observations; sparse histories show explicit point counts and stored marks |
| A chart without a value scale, benchmark or trade context was difficult to interpret | Added dollar scale, date coverage, exact hover/keyboard tooltip, SPY reference and simulated entry/exit markers |
| Market closure, missing prices and idle strategy behavior looked alike | Forward status distinguishes a closed market and the next daily processing window; monthly/hold/event strategies explain their cadence |
| Historical replay was buried behind a tab | Added a main-view historical launch card with available coverage, dates and job status |
| Automatic replay insisted on 63 sessions plus warm-up | New default replays every eligible saved session after warm-up, with explicit recent-window alternatives; legacy fixed defaults upgrade to all-history mode |
| Failed jobs could remain permanently suppressed by their input key | Failed/interrupted replay retries after a scheduling cooldown, while explicit cancellation and PAPER Pause/STOP remain effective |
| More older source days could arrive without triggering another replay | Input identity includes stored session/row coverage, not only the latest date |
| The historical page displayed final totals without the underlying path | Each run has a separate persistent ledger and compressed daily snapshots. First/previous/next/latest, date slider and playback show the selected day's actual state |
| End-of-run positions could misrepresent what was known earlier | Open marks, stops, cash, pending orders and decisions are captured on each simulated day; earlier views exclude later exits and P/L |
| The compact JSON report retained only 500 trade details | The full replay ledger remains available; session trade views page the complete archived history |
| Useful contract candidates were hidden on Stocks | Options now includes the existing calls/puts screener, with cost, break-even, spread, OI, volume and timestamps |
| One option type could occupy the entire global shortlist | Candidate selection reserves up to ten quote/liquidity candidates per side; insufficient candidates stay insufficient |
| Social, unusual-volume and learning evidence could be conflated | Options visibly separates contract quality, observed unusual activity, learning findings and social discovery; ticker mentions are not described as contract buying |
| Setup could imply a live Schwab connection solved every historical data requirement | Settings explains each source's actual role, links and limitations, including the unimplemented historical-options import requirement |

## Remaining priorities

P0 means a trust, correctness or core-completion problem. P1 means a major research-workflow improvement. P2 means an
advanced capability that should follow sound data and evaluation. Entries below are requirements, not claims of delivery.

| Priority | Finding / missing capability | Required implementation and acceptance check |
|---|---|---|
| P0 | Viewed Schwab charts and broad-market research still use separate stores/clocks | Introduce a capability-aware provider router and a shared provenance model. Use Schwab first where authorized and supported, preserve market-wide denominators, and never declare a complete session from a few symbols. Test stale bulk data with fresh watched-symbol quotes |
| P0 | Chain observations are mutable outside Learning's retained sources | Archive point-in-time chains for research and evaluation with receipt/quote time, source, identity and schema version; deduplicate without losing revisions. Bound disk/CPU scheduling visibly rather than silently dropping required history |
| P0 | Historical option executions cannot be inferred from underlying candles | Build a licensed historical bid/ask importer and validate coverage before enabling genuine historical option backtests. Show an in-app coverage matrix and import progress. No fabricated fills or claims that a purchase alone integrates a provider |
| P0 | Quote-based activity is not signed trade flow | Add a licensed transaction-level adapter if flow/sweep/aggressor claims are wanted. Preserve prints, corrections and sequence rules; unknown direction remains unknown. Cumulative volume, premium proxy and volume/OI must keep their present qualified labels |
| P0 | Research universe and fundamental history are not a full vintage archive | Add dated security membership, delistings, ticker changes and filing revisions. Counterfactual tests must prove later splits, revisions and delistings cannot change earlier decisions; keep survivorship qualifications until covered |
| P0 | Expired Learning positions can lack verified settlement | Add instrument-specific exercise/assignment/settlement, deliverables and corporate-action handling, or quarantine unsupported contracts from executable results. Preserve losses, required financing and unresolved valuations |
| P0 | Learning's current empirical promotion hurdle is exploratory | Add frozen experiment manifests, purged chronological train/validation/test blocks, nested selection, multiple-comparison accounting, failed-hypothesis retention and an untouched forward confirmation period. Repeated fills, exposure, reused tests and funding cannot inflate independent evidence |
| P1 | Option rankings are mostly activity/quote-quality lists rather than complete trade theses | Build distinct bullish/bearish/volatility idea lanes. Every candidate needs a hypothesis, horizon, catalyst, quoted structure, invalidation, liquidity/cost checks, scenarios and evidence grade. A large call print alone is not a bullish buy signal |
| P1 | No unified explanation of selection versus sizing/timing gains | Compare matched-information experiments on identical sources. Decompose underlying return, entry/exit, structure, exposure and cost effects; include finite-capital and simple benchmark books with matched dates |
| P1 | Market-wide scan coverage can be mistaken for the listed universe | Report eligible/listed/scanned/fresh/rejected counts by source and feature, with actual last-success times and queued workload. Surface reasons for exclusions and let the user prioritize a symbol from the UI |
| P1 | Full research-to-scenario handoff still takes extra navigation | Deep-link the exact OCC contract, quote timestamp and source into the existing calculator. Add editable multi-leg comparisons, scenario heatmaps, volatility shocks and sizing against available capital, with exercise/model qualifications |
| P1 | Saved work is fragmented across screens | Add saved screen definitions, side-by-side company/ETF/contract comparison, notes, immutable idea snapshots and a watchlist inbox. Restore filters and scroll position across navigation and updates |
| P1 | Event timing and impact need clearer separation | Separate confirmed earnings dates from estimated windows; show ex-dividend, macro releases and known corporate actions in a calendar. Rank using information publication time, not later revised dates |
| P1 | Alerts are not yet a complete product workflow | Add deduplicated in-app alerts for new qualified ideas, changed thesis, stale data and failed jobs. Show why it fired and why it stopped qualifying. External notifications require separately configured authorization |
| P1 | Portfolio context is weaker than individual idea context | Add aggregate delta/gamma/vega, sector/factor concentration, correlated positions, gap/volatility stress and expected cash/collateral usage. Display estimated/unavailable fields instead of zeroing missing risk |
| P1 | Financial chart behavior is inconsistent between screens | Standardize price/value axes, time zones, crosshairs, cursor data, touch inspection, keyboard access, sparse-data and missing-session gaps across profiles, calculators, PAPER and Learning |
| P1 | Scores and evidence summaries can look more certain than their inputs | Show coverage and calibration beside scores. Separate activity, forecast direction, confidence and practical executability. Backtest leaderboards need cost sensitivity, sample counts and benchmark-relative uncertainty |
| P1 | Failures need observable recovery rather than generic waiting | Add per-feature last progress, retry time, structured failure categories and GUI retry/repair actions. Fault-test token expiry, 403/429, maintenance HTML, malformed schemas, disk pressure, interrupted jobs and restarts |
| P2 | Advanced market analytics are not yet justified by available feeds | Add volatility term structure/skew surfaces, liquidity-adjusted strategy optimization, hedging/roll comparisons and transaction-derived exposure only after their required data, licensing and validation exist |

## Release gates and build sequence

1. Complete and verify the PAPER fixes above: sparse data, real polling, earliest eligible replay, chronology, trade detail
   retention, market closure, cancellation/resume and desktop/mobile inspection. Do not replace real sparse observations
   with a more exciting invented curve.
2. Close the P0 data-provenance and execution gaps. Publish a machine-readable capability/coverage report for each feature.
   Provider disconnection must degrade only dependent features and preserve all previously stored evidence.
3. Build the ranked trade-thesis and research-to-calculator workflow, then saved comparisons and alerts. Evaluate complete
   user tasks: find an idea, understand why, compare downside/cost, simulate it and locate its subsequent result.
4. Prove selection and learning with prospective, net-of-cost, held-out measurements and finite-capital transfer checks.
   Report failed ideas and unavailable data with the same prominence as favorable results.
5. Add advanced analytics after those gates, with measured latency, resource use, keyboard/touch accessibility and recovery
   checks. Competitive superiority requires a defined benchmark and observed results; it is not established by feature count.

The build environment has no operator brokerage credentials. Synthetic engine/browser tests validate code paths and
temporal isolation; public competitor page retrieval validates only those descriptions. Neither proves live entitlement,
real fill capacity, prediction accuracy or profitable trading.
