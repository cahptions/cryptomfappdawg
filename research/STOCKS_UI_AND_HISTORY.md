# Stocks interface and Schwab chart history

## User-visible changes

Stocks opens a browsable screen of ranked research lists and option contract candidates. Social input does not
enter market/options activity ranking. Contract candidates are descriptive quote/liquidity screens, not estimated
expected profit or recommendations. Saved bid/ask, volume, open interest and timestamps remain visible.

Stock charts show 1M, 3M, 6M, 1Y, 5Y and Max. The old 400-observation cap is removed (20,000 safety cap on stored
bulk chart reads). 5Y means five calendar years ending at the latest observed bar. Max means available history,
not an assurance of IPO-to-present coverage. The page states first/last dates and observation count. Long candle
views aggregate daily records into calendar weeks/months: first open, maximum high, minimum low, last close
and summed volume. No missing daily price is filled. Tooltips and labels identify the interval and dates.

Candles show a cursor-following rectangular OHLC/volume/change tooltip, support arrow keys/Home/End/Escape,
and update the headline while inspecting historical dates. Leaving restores the latest stored price or
sufficiently recent Schwab quote.

Clicking a chain strike opens the multi-leg P/L calculator. It supports up to eight buy/sell call/put legs,
quantity, actual saved strikes/expirations with selectors/sliders, optional assumed premiums, scenario
underlying/date/IV, and editable rates/dividend yield/fees. It submits no orders and never changes PAPER.

Common-expiry payoff and break-evens are piecewise exact under the stated standard-contract/fee assumptions.
Unlimited tails are derived analytically, not inferred from plotted price bounds. Earlier marks use BSM;
missing inferred IV leaves those marks unavailable. Mixed-expiry maximum loss/profit and exact terminal
break-evens remain unknown; scenarios use one specified spot at earlier expirations and do not simulate
assignment/path dependence. Model limitations are available in the calculator.

Evidence runs/cancels a fixed-argument local child with progress and readable comparisons, checks local data
health and downloads a credential-free diagnosis. Its separate report does not feed Analyzer.weights() or
mutate the forward PAPER book. Normal use requires no terminal evaluation command.

## Schwab-first scope and provenance

With valid existing Schwab credentials, stock-page quotes and daily chart history refresh independently of
long-running collection/backfill. A CSRF-protected POST registers active-page interest. A lifespan task processes
at most two histories and one <=100-symbol quote batch per pass through the existing shared rate limiter.
Quote attempts are paced at 30 seconds; history at 15 minutes with a five-minute failed-attempt cooldown.
Only pages active in the last two minutes are served. Global STOP/PAPER Pause is respected.

Requests use GET /marketdata/v1/pricehistory with year/20, daily/1, extended hours off. Public Schwabdev
documentation examples were used to check the request/response shape; no operator-authorized live history
request was made here. Registry status remains to_verify. Actual entitlement/history length may vary.

The additive stk_stock_views table stores provider candles and quote metadata separately. Existing history
survives empty/malformed/provider failures. Schwab candles receive no local split adjustment: its adjustment
policy has not been independently verified. Coverage disclosures state the basis. Prices from different bases
are not spliced. Quote timestamps are shown; delayed/stale quotes are not presented as live prices. Existing
bulk history remains available when no Schwab history is saved.

Massive still provides complete broad-universe unadjusted daily bars and corporate actions for cross-sectional
research and forward PAPER. A single-symbol download must not insert stk_bar_days or silently advance the
whole market/PAPER session. The System warning distinguishes bulk-close failures from independently available
per-stock Schwab quotes/charts/options. This release cannot remove a provider entitlement/publication delay.

## Validation

New pure tests cover long-history parsing, twenty-year request parameters, source separation, failed-download
preservation, quote freshness and bounded refresh; calculator tests cover calls/puts, debit/credit spreads,
straddles/ratio spreads, unlimited tails, fees, missing quotes/IV, invalid inputs and mixed expiries.
API tests check CSRF and read-only GET boundaries. Browser workflows cover desktop/phone ranges and candle
intervals, strike-click calculator and multiple expirations, GUI health, actual owned evaluation start/cancel
and completion without PAPER position/event mutation. All browser data is synthetic.

Final check counts and published commit are reported in the release response. The requested PAPER trade-view
redesign and historical strategy backtests are queued separately, at the user's request to finish this work first.
