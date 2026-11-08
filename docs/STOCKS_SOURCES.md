# KESTREL Stocks: data sources

**Continuation note (2026-10-21, updated 2026-11):** the source checks below are Claude's saved M1 evidence. Schwab
was added afterwards as the quote/options adapter, with a private local HTTPS callback and Tailscale only as a
fallback. The build machine never made an authenticated Schwab request; the operator's own Schwab doctor later
confirmed authorization and a live quote (the follow-up below), and the HP now collects its option chains from
Schwab. Entitlements beyond that, the full live schema and Schwab's registration policy are not verified here.
[Schwab setup and evidence limitations](SCHWAB.md). A fresh install still starts on Tradier (the configured
default) until the operator connects Schwab. [Start the desk](STOCKS.md).

Every source was checked live on 2026-10-21 from the build machine, for reachability, format, limits, terms and
price. The probes are recorded in `research/evidence/2026-10-21/stocks_sources_verification.json`, and trimmed real
responses are in `tests/fixtures/stocks/`. Every plan used is free. Keys and the SEC contact live only in
`~/.config/desk/secrets.env`.

**Operator follow-up:** a laptop Schwab online doctor subsequently confirmed authorization and one market
quote, with no configuration problems. This does not validate all option chains or trading performance.
The laptop's FINRA JSON error matched the default CSV header format; the client now explicitly requests JSON
and accepts schema-checked CSV too. Pagination counts received rows before filtering missing measurements.
FRED is requested as CSV and its series/date columns are checked. An HTML or other non-data response stays
unavailable. The operator's subsequent VIX CSV probe returned a St. Louis Fed **Site Maintenance** HTML page.
This requires source recovery, not different API credentials; the next scheduled rates refresh can retry.
Recovery and the separate credit-spread endpoint were not verified by that probe. The extra FINRA CSV fixture is a
synthetic serialization of existing recorded values, not additional live evidence.

## Set up in Settings (stock prices, SEC contact, and optional options)

Use **Settings → Start here · free stock research** to save these privately; no file editing is needed.
Then click **Start research**. No-signup public feeds start with the collector. Connected Schwab supplies individual
stock quotes/charts and option chains; Massive provides the separate broad daily-price archive. SEC email requires
no registration, and Tradier is optional while waiting for Schwab.

| Put in `secrets.env` | What it unlocks | Where to get it |
|---|---|---|
| `MASSIVE_API_KEY=...` | Broad daily bars, splits/dividends; company overview, news and branding where entitled | [Massive dashboard](https://massive.com/dashboard) → Stocks **Basic** ($0) → API keys |
| `FINNHUB_API_KEY=...` (optional) | Analyst recommendations, the last four earnings quarters, and earnings calendar/estimates; with it, every stock's announced report dates for the next four weeks are stored daily (four requests) for Plays' earnings sections and the stock page (`desk.stocks.earnings_dates`). It is the only free forward earnings calendar the app can use: Massive Stocks Basic has none (earnings are a paid partner add-on), Schwab's quotes carry only the last report date, the SEC publishes filings after the fact, and Nasdaq's calendar refuses programs. Without the key, report dates are estimated from past 8-K results filings (±7 days around the median gap), labelled as estimates | [Finnhub registration](https://finnhub.io/register) → free key → Settings → optional company-data key |
| `TRADIER_TOKEN=...` | Option chains: bid, ask, volume, open interest for every strike | tradier.com → developer sandbox (free) → API access token. Leave `stocks.tradier_env = "sandbox"`. With a Tradier Brokerage account instead, use its token and set `"production"` for real-time data. |
| `SEC_CONTACT_EMAIL=you@...` | Company list, daily filings, insider trades, fails-to-deliver | Your own email address. The SEC asks every program to name a contact, and www.sec.gov refuses requests without one. It goes only to the SEC. |

Without these, the desk still runs and says exactly what is missing (System page and `desk stocks doctor`):
- no connected stock-data provider: no new prices;
- neither Schwab nor Tradier connected: no new option chains;
- no contact: SEC collection waits; Nasdaq listings and other no-signup feeds continue independently.

## Company-page sources and verification status

The stock page combines issuer filings with display-only company context. Cached sections keep their source and
observation date; unavailable values remain missing. The saved source review is
[stock_page_sources.json](../research/evidence/2026-10-29/stock_page_sources.json). Its keyless Wikimedia probes were
successful. Massive, Finnhub and the additional Schwab quote sections still use documented/synthetic response examples
until an authenticated laptop response confirms them. A reachable public documentation URL does not verify entitlement.

| Source | Company-page use | Access and practical limits |
|---|---|---|
| [Massive ticker overview](https://massive.com/docs/rest/stocks/tickers/ticker-overview) | Description, employees, address, website, phone, industry, exchange, shares and branding | Documented as included in Basic, updated daily; actual key/plan access remains to verify. Requests share the existing Basic budget. Branding is fetched server-side; the API key never reaches the browser. |
| [Massive news](https://massive.com/docs/rest/stocks/news) | Headlines, publisher, time, article links and provided ticker sentiment | Documented as included in Basic, updated hourly with two years on Basic; not a real-time news guarantee. Keyed response still to verify. |
| [Wikidata](https://query.wikidata.org/) | Fallback founded date, dated headcount, headquarters, website and linked English Wikipedia article | Keyless; descriptive User-Agent. Missing labels are dropped. SEC/issuer data has priority; an issuer's Form 4 officer title takes priority for CEO. The saved probe encountered throttling, so retries/cache can delay context. |
| [Wikipedia summary](https://en.wikipedia.org/api/rest_v1/) | Linked fallback company description | Keyless, cached; article attribution/CC BY-SA. Descriptive context is separate from trading features and historical evidence. |
| [Finnhub OpenAPI specification](https://finnhub.io/static/swagger.json) | Recommendations, earnings results versus estimates, next-report calendar | Optional free key entered in Settings. `/stock/earnings` documents the last four quarters; calendar free history is one month plus updates. Earnings estimates are separate from SEC-reported figures. `/stock/price-target` is premium and unused. Actual key access/rate entitlement remains to verify. |
| Schwab quote extra sections | Available quote/fundamental/reference/regular/extended statistics | See [Schwab field verification](SCHWAB.md#additional-company-statistics). Fields and units are accepted only when present; unverified dividend-yield and borrow-rate units are not inferred. |

The **Company data sources** panel shows received sections and failures. Add an optional key in Settings, then leave
the app collecting for the queued requests to run. No terminal repair is required to use the source controls. No new
paid API is needed for these sections; a missing optional key does not stop quotes, collection or PAPER activity.

**Discovery:** Trending ranks measured stock/qualified-options activity only. WallStreetBets has a separate Home tab
showing observed mentions, reported upvotes and its source time. Social attention cannot qualify or rerank Trending.

## Sources used

| Source | Gives | Limits and facts (verified) | Key | Terms |
|---|---|---|---|---|
| SEC EDGAR, data.sec.gov | Company facts (XBRL: revenue, earnings, cash flow, balance sheet, shares) with the date each value was filed; submissions (every filing, 8-K item numbers, acceptance time, SIC industry) | Fair access: ≤ 10 requests/s across SEC hosts. The desk uses 4/s. Accepted requests without a contact. | none | Public government data |
| SEC EDGAR, www.sec.gov | Company list with exchange; daily filing index; Form 4 insider filings (every 4 hours; after downtime up to ten days, or five minutes, a pass until caught up); the quarterly Insider Transactions Data Sets for the nine newest quarters (about 9 MB each, one a pass until all nine are in, then a weekly look for a new quarter); fails-to-deliver (two files a month, kept for the last 24 months: four files a pass until all are in) | **403 without a contact address in the User-Agent**, 200 with one | contact | Public government data, fair access |
| Nasdaq Trader | Every listed security: Nasdaq (5,619 rows) and NYSE / NYSE American / Arca / Cboe BZX / IEX (7,674 rows). ETF and test-issue flags. | Rebuilt daily | none | Public reference data |
| FINRA Query API | Consolidated short interest, twice a month: shares short, previous, average volume, days to cover. The newest settlement daily, and 48 settlements (two years) kept: four older ones a pass until all are in. A candidate date FINRA answers empty for is not asked about again. | 5,000 records a request. A settlement date is a partition key. Unpublished dates answer 204. Latest on 2026-10-21: 2026-09-30, 22,581 rows. | none | FINRA public data |
| FINRA CDN | Daily short-sale volume per symbol (Reg SHO, consolidated). Daily, reaching back to the last stored day (at most 45 days, the first load too), so a downtime is filled; 20 files a pass. | One file a trading day | none | FINRA public data |
| US Treasury | Daily par yield curve (3-month, 2-, 10- and 30-year) | One CSV a year | none | Public government data |
| FRED (graph CSV) | High-yield credit spread (BAMLH0A0HYM2), VIX close (VIXCLS) | **Unreliable from the build host:** one 200, then dropped connections and timeouts. Treated as optional, and never blocks the refresh. | none | FRED terms; series owned by ICE and Cboe |
| ApeWisdom | Mentions, upvotes, rank and 24-hour change per ticker on r/wallstreetbets, r/stocks, r/options and more | 100 per page; wallstreetbets had 681 tickers | none | Public API |
| Massive (formerly Polygon.io) Stocks Basic | Grouped daily bars: **every US stock's day in one request**; splits; dividends; the market-wide news feed with per-stock sentiment (read by the News desk, and the stock page's and Events headlines: [News](STOCKS_NEWS.md)); company details and icons for the stock page | $0, personal licence, **5 requests/min, 2 years of history, end of day**. No quotes, snapshots or flat files. A day costs one request, and the 2-year backfill about 500 requests (≈ 1¾ hours, in the background). | `MASSIVE_API_KEY` (query parameter `apiKey`) | Personal ("individual use") |
| Massive Options Basic (free, same key) | Real daily bars of individual option contracts, two years back, weeklies included: the prices the historical replay traded (`desk.stocks.options_history`) and the Lottos replay on real past prices (`desk.stocks.lotto_history`) | One contract (or one contract listing) per request, within the same shared 5 requests/min; no open interest, IV, quotes or snapshots, so it cannot build today's chain | `MASSIVE_API_KEY` | Personal ("individual use") |
| Schwab market data (approved personal app) | Stock quotes and charts, option expirations and chains with Schwab's IV and greeks: the option source once connected. Daily price history (up to 20 years a stock in one request) adds the years before Massive's two to the stored prices, checked and un-adjusted per stock ([method §9](STOCKS_METHOD.md)) | Paced at 90 requests/min (provisional; Schwab's own limit is not published for this app); a weekly browser sign-in; a chain Schwab marks delayed is refused. GET market data only. | App key and secret in Settings, sign-in token beside secrets.env | Market data only; no account or order endpoint ([SCHWAB.md](SCHWAB.md)) |
| Tradier market data | Option expirations and chains: bid, ask, last, volume, open interest, average volume. Stock quotes, history, market calendar. | Sandbox: free, **15-minute delayed**, no greeks, 60 requests/min. Brokerage account: real-time, 120/min. KESTREL computes IV and greeks itself either way. | `TRADIER_TOKEN` (Bearer) | Market data only; the desk calls no order or account endpoint |

## Checked and not used

- **Cboe delayed quotes (cdn.cboe.com):** it works, and it has daily history back to 2004 and full option chains with IV and greeks. But Cboe's website terms allow viewing and downloading "one copy of the Materials for your personal non-commercial use", and forbid storing them "in an electronic retrieval system". A research database is exactly that, so it is not used.
- **OpenFIGI (CUSIP → ticker):** a client exists in the code (`FeedsClient.cusip_tickers`), but nothing calls it. KESTREL does not collect 13F institutional holdings, so none are mapped or shown.
- **Stooq:** the connection was reset from the build host. Not used.
- **Reddit API:** needs an OAuth app (public JSON answered 403). ApeWisdom already counts Reddit mentions.
- **Yahoo Finance and other unofficial APIs:** their terms forbid this kind of use.
- **Robinhood:** no public stock or options API. Login-based libraries are against the ground rules.

## What these limits mean

- **History depth is 2 years from Massive**, and with Schwab connected `price_history_years` more (default 10) from
  Schwab's price history, each stock checked against Massive's bars and the split record before it is stored
  ([method §9](STOCKS_METHOD.md)). Without Schwab the depth is two years and grows by a day each day.
- **Fundamentals go back further**: SEC company facts carry every filing's values and filing dates.
- **Survivorship:** Massive's two years are survivorship-free: companies delisted during them come from Massive's reference list of inactive tickers, with their daily bars, filings and facts ([method §8](STOCKS_METHOD.md)). Schwab's earlier years hold only companies still listed when that window began (no free source has delisted companies' older prices), and evaluations report the two periods separately.
- **Option chain history (IV, open interest) starts when the desk starts collecting.** Massive Options Basic keeps two years of individual contracts' daily prices, which the replays use, but no past IV or open interest, so IV rank needs a year of the desk's own daily snapshots. Until then the options desk compares IV with realized volatility and says so.
- **Stock history is end-of-day** (Massive's grouped daily bars). **Option chains are Schwab's current quotes** once Schwab is connected (a chain Schwab marks delayed is refused); on Tradier's sandbox they are 15 minutes delayed. Paper fills follow from what is collected: stocks at the next session's open, options at bid/ask from the collected chain at its own timestamp. Nothing pretends to be real-time beyond that.
