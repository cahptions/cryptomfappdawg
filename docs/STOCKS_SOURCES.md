# KESTREL Stocks: data sources

**Continuation note (2026-10-21):** the source checks below are Claude's saved M1 evidence. Schwab is a new,
optional quote/options adapter, with a private local HTTPS callback and Tailscale only as a fallback. No
authenticated Schwab probe was performed here; its credentials, entitlements, live schema and registration
policy remain to verify. [Schwab setup and evidence limitations](SCHWAB.md). The original Tradier sandbox
remains the default until the operator explicitly selects Schwab. [Start the desk](STOCKS.md).

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
Then click **Start research**. No-signup public feeds start with the collector. Stock charts/history need the
Massive key; SEC email requires no registration, and Tradier is optional while waiting for Schwab.

| Put in `secrets.env` | What it unlocks | Where to get it |
|---|---|---|
| `MASSIVE_API_KEY=...` | Stock prices: every US stock's daily bar, splits, dividends (2 years) | massive.com → sign up → Stocks **Basic** ($0) → dashboard → API keys |
| `TRADIER_TOKEN=...` | Option chains: bid, ask, volume, open interest for every strike | tradier.com → developer sandbox (free) → API access token. Leave `stocks.tradier_env = "sandbox"`. With a Tradier Brokerage account instead, use its token and set `"production"` for real-time data. |
| `SEC_CONTACT_EMAIL=you@...` | Company list, daily filings, insider trades, fails-to-deliver | Your own email address. The SEC asks every program to name a contact, and www.sec.gov refuses requests without one. It goes only to the SEC. |

Without these, the desk still runs and says exactly what is missing (System page and `desk stocks doctor`):
- no price key: no prices;
- no token: no option chains;
- no contact: SEC collection waits; Nasdaq listings and other no-signup feeds continue independently.

## Sources used

| Source | Gives | Limits and facts (verified) | Key | Terms |
|---|---|---|---|---|
| SEC EDGAR, data.sec.gov | Company facts (XBRL: revenue, earnings, cash flow, balance sheet, shares) with the date each value was filed; submissions (every filing, 8-K item numbers, acceptance time, SIC industry) | Fair access: ≤ 10 requests/s across SEC hosts. The desk uses 4/s. Accepted requests without a contact. | none | Public government data |
| SEC EDGAR, www.sec.gov | Company list with exchange; daily filing index; Form 4 insider filings; fails-to-deliver (twice a month) | **403 without a contact address in the User-Agent**, 200 with one | contact | Public government data, fair access |
| Nasdaq Trader | Every listed security: Nasdaq (5,619 rows) and NYSE / NYSE American / Arca / Cboe BZX / IEX (7,674 rows). ETF and test-issue flags. | Rebuilt daily | none | Public reference data |
| FINRA Query API | Consolidated short interest, twice a month: shares short, previous, average volume, days to cover | 5,000 records a request. A settlement date is a partition key. Unpublished dates answer 204. Latest on 2026-10-21: 2026-09-30, 22,581 rows. | none | FINRA public data |
| FINRA CDN | Daily short-sale volume per symbol (Reg SHO, consolidated) | One file a trading day | none | FINRA public data |
| US Treasury | Daily par yield curve (3-month, 2-, 10- and 30-year) | One CSV a year | none | Public government data |
| FRED (graph CSV) | High-yield credit spread (BAMLH0A0HYM2), VIX close (VIXCLS) | **Unreliable from the build host:** one 200, then dropped connections and timeouts. Treated as optional, and never blocks the refresh. | none | FRED terms; series owned by ICE and Cboe |
| ApeWisdom | Mentions, upvotes, rank and 24-hour change per ticker on r/wallstreetbets, r/stocks, r/options and more | 100 per page; wallstreetbets had 681 tickers | none | Public API |
| OpenFIGI | CUSIP → ticker (for 13F institutional holdings) | Without a key: 25 requests/min, 10 identifiers each | none | Free mapping service |
| Massive (formerly Polygon.io) Stocks Basic | Grouped daily bars: **every US stock's day in one request**; splits; dividends | $0, personal licence, **5 requests/min, 2 years of history, end of day**. No quotes, snapshots or flat files. A day costs one request, and the 2-year backfill about 500 requests (≈ 1¾ hours, in the background). | `MASSIVE_API_KEY` (query parameter `apiKey`) | Personal ("individual use") |
| Tradier market data | Option expirations and chains: bid, ask, last, volume, open interest, average volume. Stock quotes, history, market calendar. | Sandbox: free, **15-minute delayed**, no greeks, 60 requests/min. Brokerage account: real-time, 120/min. KESTREL computes IV and greeks itself either way. | `TRADIER_TOKEN` (Bearer) | Market data only; the desk calls no order or account endpoint |

## Checked and not used

- **Cboe delayed quotes (cdn.cboe.com):** it works, and it has daily history back to 2004 and full option chains with IV and greeks. But Cboe's website terms allow viewing and downloading "one copy of the Materials for your personal non-commercial use", and forbid storing them "in an electronic retrieval system". A research database is exactly that, so it is not used.
- **Massive Options Basic (free):** one contract per request at 5 a minute, with no open interest, IV, quotes or snapshots. It can't build a chain.
- **Stooq:** the connection was reset from the build host. Not used.
- **Reddit API:** needs an OAuth app (public JSON answered 403). ApeWisdom already counts Reddit mentions.
- **Yahoo Finance and other unofficial APIs:** their terms forbid this kind of use.
- **Robinhood:** no public stock or options API. Login-based libraries are against the ground rules.

## What these limits mean

- **History depth is 2 years** for anything that needs prices (Massive's free plan). So:
  - 12-month momentum has about a year of point-in-time history to be tested on;
  - "percentile of its own history" means its own 2 years, not 5–10;
  - the report cards say so.

  The desk keeps every day it stores, so the depth grows from here.
- **Fundamentals go back further**: SEC company facts carry every filing's values and filing dates.
- **Survivorship:** the price universe is today's listings. Companies delisted before the desk started have no prices; SEC filings for them still exist but cannot be scored.
- **Option history starts when the desk starts collecting.** Free sources have no historical chains, so IV rank needs a year of the desk's own daily snapshots. Until then the options desk compares IV with realized volatility and says so.
- **Prices are end-of-day; options are 15-minute delayed** (sandbox). Paper fills follow from that: stocks at the next session's open, options at bid/ask from the snapshot. Nothing pretends to be real-time.
