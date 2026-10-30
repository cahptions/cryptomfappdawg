# KESTREL Stocks — start here

The saved Claude branch completed the research/data modules, eleven desks, validation and PAPER engines
(M1–M6, through `972565b`). The continuation connects those modules to operator commands, a local dashboard
and a Schwab **market-data-only** adapter. There is no Schwab account/order endpoint or LIVE trading in this desk.
Code and synthetic tests cannot establish a profitable strategy or finish prospective validation.

## 1. Open the Stocks app — PAPER starts automatically

After updating to the continuation branch and installing the locked dependencies:

```bash
cd ~/kestrel
git fetch origin
git switch codex/finish-schwab-stocks
git pull --ff-only
uv sync --frozen
uv run desk stocks doctor
uv run desk stocks dashboard --open
```

The dashboard is **http://127.0.0.1:8767**. Leave its terminal open. This is separate from Robinhood Crypto
on8766 and the Solana desk on8765; the Stocks command neither stops nor starts those collectors.
If the browser does not open automatically, enter the full dashboard address in its address bar.

The default **Home** is a stock/options research hub: benchmark ETF prices, a top10 Trending/Rising trends/
Gainers/Losers table, value/quality/squeeze/insider collections, options intelligence, breadth, sectors and a
watchlist. **Options** has its own overview; **Plays** ranks the options of the day in twelve categories with
each contract's greeks and three chances of finishing in the money ([STOCKS_PLAYS.md](STOCKS_PLAYS.md));
**Stocks** opens the full saved lists. A stock page combines
split-adjusted line/candlestick charts and volume, financial statistics, the committee and eleven desks,
bull/bear evidence and a dated calls/puts table. Missing observations remain missing; a chart breaks at
missing sessions rather than manufacturing candles. Top10 means up to ten qualifying names, never padding.
These are daily-close research views, not a live intraday ranking service.

Install and open the Stocks application-menu shortcut, like Crypto and Solana:

```bash
uv run desk open --desk stocks --install
```

Then find **KESTREL Stocks** in the Linux Mint menu; right-click it to add it to your panel or favorites. It opens
in its own Chrome/Chromium app window when available, otherwise a new browser window. Its lime icon and browser
profile are separate from the other desks. The first window uses the screen's full work area; later opens restore
its saved position. The command reuses an answering dashboard or starts it in the background, so a newly started
dashboard needs no open terminal. Opening the dashboard automatically starts one combined PAPER/collection
worker. Installing the shortcut preserves other launchers and does not install login services.

You can also open the Stocks desk with `uv run desk open --desk stocks`; it starts only the dashboard if
needed and opens its app window. Stocks dashboard startup also starts its PAPER/collection worker unless
previously paused. The existing `desk up/down` services still
manage the two older desks. After pulling code while a Stocks dashboard is already running, stop its old
terminal process with Ctrl+C and restart it so the backend and page assets use the same version.

Settings has the private Schwab credential form and a connection guide. HTTPS for its OAuth callback does
**not** require buying a domain. Start with the local callback and use Tailscale only if needed:
[complete connection guide](SCHWAB.md). Successful login alone does not verify data entitlements; run
`uv run desk stocks doctor --online` for one read-only SPY quote request.

## 2. Set up the free sources

Schwab supplies quotes/options for an approved app. Broad daily stock history still uses a free Massive Stocks
Basic key. An SEC contact email identifies the research client to EDGAR. Tradier is the existing alternative
options source; its sandbox is delayed. Choose one options provider explicitly in Settings. Schwab is not a paid
data requirement, and missing sources stay visibly missing.

Open **Settings → Start here · free stock research**:

1. Click **Open Massive signup**. Create a free account, select **Stocks Basic ($0)**, and copy its API key
   from the account dashboard into the **Stock price history** field. Do not select a paid upgrade.
2. Enter your email in **Company filings · SEC**. There is no SEC signup or key; the address identifies requests.
3. Skip **Options while waiting for Schwab** unless you already want to use a Tradier sandbox token. It is optional;
   waiting for Schwab does not block stock price history, charts or price-based research.
4. Click **Save research sources** and return to **Home**. The PAPER worker starts with the dashboard and
   reloads these sources on its next pass. Downloaded data appears as it is stored; eligible strategies simulate
   trades automatically. **Pause PAPER** pauses both simulation and collection and remembers that choice across
   app restarts. **Start PAPER** resumes both. No extra PAPER terminal command is required.

Closing a browser window leaves a background dashboard running. Stopping the dashboard process requests a clean
worker stop; reopening the dashboard automatically starts it again unless explicitly paused. Network/rate-limit
waits can be interrupted; synchronous database/PAPER transactions finish atomically before the worker unlocks.
Existing terminal workers are observed, not signalled or duplicated. When an external worker finishes, the
background startup monitor starts PAPER. Unexpected worker exits remain visible instead of restarting endlessly.

No file editing is needed. Settings writes the existing owner-only secrets file atomically, preserves other desks'
keys, and leaves Schwab authorization intact. Blank fields preserve saved values; saved keys/email are never
returned to the browser. A running collector reloads these auxiliary sources on its next pass. Switching the
Schwab/Tradier connection still requires pausing collection first. **Keep the dashboard terminal open**.
The Schwab form has **Pause collector to save** for its owned research collector, and **Save privately** stays
disabled until the collector stops. A collector started outside this dashboard is reported but cannot be paused by
the dashboard: press Ctrl+C only in its `desk stocks collect/paper/refresh` terminal, never the dashboard terminal.
Pausing from Settings preserves unsaved form values. Saved Schwab key/secret fields stay blank on reload, with
individual **Saved on this laptop** indicators; blank inputs keep the stored credentials. Restarted-dashboard
session tokens are renewed once on submission without weakening the same-origin/CSRF boundary.

Nasdaq listings, mentions, FINRA short activity and Treasury rates are already integrated and need no signup;
they run when collection starts. They do not supply the missing stock price history. Missing optional SEC mapping
cannot discard successfully downloaded listings. If you add an SEC contact after collecting listings without one,
the next pass retries the mapping rather than waiting24hours. **System** shows actual successes/errors; a saved
key indicates configuration, not verified provider access.

The mockup's displayed prices were examples. Production does not include a demo-price fallback. Benchmark charts
can populate as the first real price sessions arrive. Research lists require at least60 actual observations per
eligible stock, sufficient price/liquidity, and any inputs required by that particular list; longer lookbacks need
more history. Backfill is downloaded history, not60 days you must wait through. There is no24-hour build gate.
Before the first list is recorded, Home shows downloaded-session counts and whether collection is running or
stopped. "No qualifying names" refers to a recorded empty list. The session count covers downloaded market
dates; it does not guarantee that every stock has enough usable observations. Use **System** for source errors.

The research-only collector maintains a **current research preview**, including re-analysis on the same closing
date as history arrives. It does not write frozen list/score history, PAPER accounts, orders or steps. Old frozen
lists (including any empty warm-up records) are preserved. A preview is labeled separately and is not prospective
evidence. The automatic worker refreshes these previews as well as advancing the separate forward PAPER record.

Source provenance and limits are in [STOCKS_SOURCES.md](STOCKS_SOURCES.md). Never paste keys, codes or token files into chat.
Environment variables override secrets.env, so remove an obsolete exported value if it overrides a new key.

## 3. Automatic collection and PAPER trading

Opening Stocks starts `desk stocks paper --continuous --price-days 20` in the background. There is no24-hour
cutoff for this app-owned worker. It checks again five minutes after each completed pass, collects up to20
missing price sessions,20 companies and5 rotating option underlyings per pass, and respects the free provider
limits. Saved history is reused. Unavailable optional sources are recorded; each strategy still requires its own
inputs and a qualifying setup. "Running PAPER" does not promise an immediate simulated trade.

**Same-evening close.**
- **When:** with Schwab signed in, about five minutes after the 16:00 New York close the worker reads every stored
  stock's regular-session open, high, low, close (`regularMarketLastPrice`, which must have traded that day) and volume.
- **How much:** about 60 requests of 100 symbols, inside the rate limit.
- **Result:** the day is saved once and the daily research and PAPER session run that evening.
- **Fallback:** if Schwab is signed out, or fewer than 80% of stocks return a usable close, nothing is saved and the day
  waits for Massive's next-morning file as before.
- **Check against Massive:** once Massive publishes the same day, its closes are compared with Schwab's (agreement
  within 0.5%). Stored bars are never rewritten. If more than 5% of stocks disagree, the same-evening close switches
  itself off and System says why. Code: `src/desk/stocks/schwab_close.py`.

Initial history must pass the minimum research warmup before the first PAPER session is consumed. Existing
positions remain subject to their normal management rules. Closed-session decisions and next-session fills
retain their original timing; historical backfill does not fabricate historical entries. STOP continues to block
new entries. Broker orders remain disabled.

Use **Pause PAPER / Start PAPER** in the app. Pausing for a credential change also pauses this worker; resume
with Start PAPER after saving/authorizing. Pause is persistent. A damaged automation preference or unexpected
worker crash is shown as an error instead of silently restarting. Logs remain in `~/desk-data/logs/stocks-research.log`.

The laptop must stay awake and connected. Tailscale does not host the app or keep a sleeping laptop online.
Advanced terminal commands still exist for an explicitly chosen bounded experiment, but do not start one while
the automatic worker runs:

```bash
uv run desk stocks paper --minutes 1440
uv run desk stocks collect --minutes 1440  # research-only alternative, no PAPER ledger changes
```

The ranked option focus is capped at150 names; the pilot rotates small batches no more often than every4hours.
It does **not** provide a daily full-universe option archive. Coverage and option signal availability are sparse,
and option histories must not be interpreted as complete daily sampling. Held contracts are included regardless
of the current rotating focus. Broader coverage needs measured API/runtime evidence before changing that budget.

Free history is rate-limited. Roughly500 initial grouped-price requests consume at least100minutes at
5/minute; the default five-day batches separated by a five-minute pause stretch the initial backfill across
several hours. Network/API failures can extend this. Fetches start with newest sessions, and stored history
is reused. A stock needs at least60 actual price sessions to be researched; some measures need200 or252.
There is no way to replace missing market history with synthetic success.

This **Stocks** experiment uses closed US equity sessions. That schedule is unrelated to continuous crypto
collection. Analysis/PAPER decisions run once per eligible published closed session after the configured
75-minute publication allowance; price/fundamental backfill and collection continue between decisions.
An unchanged processed session is not recomputed on every poll.

If the laptop sleeps, unavailable observations remain unavailable. On recovery, existing PAPER exposure can
be marked/managed through stored missed sessions, but new entries are not discovered retrospectively. Those
recovery marks are simulations and must be segmented from uninterrupted prospective observations. An order
created after an opening cannot fill at that past opening. A PAPER session commits atomically across accounts,
so a failed transaction can be retried without a half-completed day.

## 4. Read the evidence

```bash
uv run desk stocks report
uv run desk stocks validate
uv run desk stocks learn
```

`report` reads the saved PAPER report. `validate` explicitly builds/checks the historical reconstruction.
`learn` explicitly runs the existing bounded candidate lab; configured `learning = "paper"` can add qualified
lab candidates to PAPER, never LIVE. These heavier calculations use the same worker lock and must run with
the Stocks collector stopped. They never run in a dashboard request. The dashboard's Evidence page reads
the last saved validation. Initial reports can honestly say there is insufficient evidence.

Stock strategies have separate **$10,000 simulated accounts**, options strategies **$50,000**, as defined by
Claude's M6 configuration. They remain separate from the earlier **$500 crypto reference** and **$100,000
crypto learning book**. Neither account capital nor historical gains are relabelled or multiplied. Limits
remain account-relative. Scores measure ranked evidence; desk "confidence" is data coverage, not a calibrated
profit probability. [Methodology](STOCKS_METHOD.md), [PAPER rules/limits](STOCKS_PAPER.md).

The **Events** tab logs unusual market days (big index, sector, theme and stock moves, broad sell-offs,
corrections, VIX spikes, sharp rate moves) by fixed rules from stored closing data, as context for reading
results. [How events are found](STOCKS_EVENTS.md).

`uv run desk stop` activates the shared STOP file. The next Stocks pass cancels pending entries, continues
managing held PAPER positions and suppresses new entries. `uv run desk resume` clears the shared STOP,
so inspect its impact on other desks before deliberately resuming.

## Data and resource boundaries

- Stocks data lives in separate `stocks-market.db`, `stocks-research.db`, `stocks-ledger-paper.db` under
  `~/desk-data/db`. Connection preferences and saved reports live under `~/desk-data`; private tokens/certificates
  live beside secrets.env. Updating code does not rewrite an existing account's initial deposit.
- Quotes keep their actual timestamps. Standard100-share contracts only; adjusted deliverables are excluded.
  Explicitly delayed Schwab chains are refused. Chain rows record their provider; one asset/session cannot mix
  providers. Legacy option records are labelled `tradier_legacy_unknown_environment`. Switching source should
  be a separate evaluation cohort, not an apparent performance improvement.
- Daily snapshots, estimated slippage, modeled option marks/exits and intrinsic expiry settlement are research
  approximations. They do not model all assignment, exercise, borrow or executable market-depth risks. The
  inherited regulatory fees are dated2025 assumptions, not verified current broker charges. These limitations
  prevent a claim of executable edge or LIVE readiness.
- One worker and one dashboard; ten maximum HTTP connections; bounded five-day/company/options batches;
  730calendar-day default price panel. No GPU or paid model. The dashboard serves at most400 chart bars,
  25rows per list,100 recent decisions/events; expensive bootstrap work is outside requests. Option raw chains
  retain45days. Archived bars and research records grow; check disk with `du -sh ~/desk-data` periodically.
- Broad analysis is postponed below1.5GB available RAM. This is a startup check, not a hard memory cap.
  Full-universe peak RAM, SSD growth, a full-duration laptop run and browser memory behavior still need actual
  measurement on the Ryzen7730U/16GiB Mint laptop. Do not run multiple broad research jobs at once.

The home view fetches at most150 stock cards with24 observations each and shows at most10 rows per featured
list. The options table reads one collected expiration and returns at most40 nearby contracts, with original
provider/timestamps. Page reads never fetch broker quotes or alter strategy rules. Watchlist updates require the
same local-origin and CSRF protection as Settings; saved watchlists retain the existing bounded collection behavior.

## Development checks

```bash
uv run pytest -q
uv run ruff check src tests
uv run mypy
```

Schwab authentication/provider tests use synthetic responses and loopback listeners. Browser fixtures are
`tests/browser/stocks_fixture_server.py` and `stocks_smoke.mjs` (Playwright/Chromium, desktop and375px).
Never run a browser fixture test that writes Settings against the operator's real data directory.
