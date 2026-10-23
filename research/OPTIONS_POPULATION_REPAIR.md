# Options population repair — 2026-10-22 America/New_York

Read this before investigating the same empty-chain screenshots again. Work is on `codex/arbitrage-v1`.

## Confirmed code defects and repair

1. `sources/schwab.py::parse_chain` rejected **every nonempty optionDeliverablesList**. Standard Schwab contracts
   also include one `STOCK` deliverable for the same underlying, with 100 units and a 100 multiplier.
   Explicitly standard deliverables now pass. Mini, nonstandard, cash, baskets, different underlyings, wrong units
   and inconsistent OCC identity remain excluded. Missing quote timestamps remain missing.
2. `collect.option_chains` could record a successful job with zero parsed contracts. Empty supported-chain
   responses now fail with per-symbol diagnostics. A failed/empty fetch cannot erase a useful stored expiry.
   Each nonempty expiry is saved before the separate underlying quote request, so that request failing cannot
   discard the chain. Raw quotes remain browsable even when unsuitable for analytics.
3. Options lists were bound to the **equity history date** and 63-session stock features. The screenshots had
   October 22 options but research stuck on October 21 because Massive returned 403. New mutable
   `stocks-options.json` publishes activity, high volume ratios, expected moves and relative IV on the options
   session independently. Raw chain browsing and activity do not require company fundamentals or stock backfill.
   Relative IV still requires 21 consecutive real closes ending on the option session; gaps fail closed.
4. A recent Massive 403 aborted the entire newest-first backfill. Denied recent dates are now deferred while
   accessible older requested dates proceed. Denied dates are never marked complete/holidays. If all recent
   requests are denied, the job remains failed. The 403 could be publication timing, credentials or entitlement;
   its exact account-specific cause is **not** established here.

## Automatic collection and presentation

- Core SPY/QQQ, held expirations, watchlist and bounded rotating focus remain covered. Scheduled collection is
  eligible every 15 minutes during 09:30–16:30 New York, rather than four hours. Missed-session recovery also
  runs after hours; parser-version upgrades trigger recollection without waiting for the old success timestamp.
- Opening a stock automatically sends a CSRF-protected **POST** for collection priority. GET endpoints remain
  read-only. New SQLite migration `stocks_market/0003_option_requests.sql` holds at most 100 coalesced requests,
  with a 24-hour request horizon and 15-minute re-request cooldown. Up to two requests join each normal bounded
  batch; the next available pass processes them. Requests do not start paused workers, change watchlists or
  place orders. A long in-flight provider/backfill job can still delay the next pass; this is not a latency SLA.
- Options preview is published before stock analysis and immediately after chain collection, before other sources.
  Home prefers newer available options activity when the composite list is empty or older, with the list's own
  date/name. Social scores do not enter the new activity lists.
- System shows per-underlying received/usable contracts and exact exclusion counts. Missing relative-IV history,
  no high-volume ratio, no near-dated ATM pair and absent chains have different messages.
- Calls/puts each receive a bounded nearest-strike allowance, with provider, session, actual quote times, total
  stored count and shown count. Old/expired captures are explicitly archived. A corrupt selected compressed expiry
  produces a recollection message rather than taking down its page. Table headers have explicit column scope.
- The raw chain view remains a research snapshot, not an executable quote. Wide/stale/unquoted contracts cannot
  enter newly computed IV/expected-move summaries. Existing risk/STOP/LIVE boundaries remain in place.

## Evidence and limits

The deliverable schema was checked against the publicly accessible Schwabdev response example, downloaded from
`https://raw.githubusercontent.com/tylerebowers/Schwabdev/main/docs/pages/api.html` (HTTP 200) on October 23 UTC
(October 22 New York). Rendered documentation:
<https://tylerebowers.github.io/Schwabdev/pages/api.html>. This is a third-party published example, **not** a live
response from the operator's account or independent confirmation of Schwab's current authenticated API schema.
The observed example contains `assetType: STOCK`, `deliverableUnits: 100.0`, multiplier 100 and nonStandard false.
Tests cover that shape across SPY, QQQ, AAPL and NVDA; quote values/timestamps in tests are synthetic.

No private Schwab/Massive credentials or laptop databases are available in this workspace. Actual account
entitlements and newly collected laptop rows require post-installation confirmation. No credentials were requested,
printed or committed. No collector on the operator's laptop was touched from the cloud.

## Validation

- Complete offline suite: **1,260 passed**, 5 live tests excluded, 139.88 seconds. Ruff and Mypy passed (219 source files).
- Tests exercise standard/adjusted identity, empty responses, partial providers, missing underlying price,
  zero equity history, a newer chain session than equity research, raw-chain preservation, current ETF relative IV,
  missing price sessions, stale/wide quotes, side balance, bounded requests and POST protection. Mutable preview
  and queued research leave the PAPER ledger and frozen evidence records unchanged.
- Chromium desktop (1366) and phone (375) checks show SPY and QQQ calls/puts. A separate reproduction of the
  screenshot combines a synthetic Massive 403, October 21 prices and October 22 Schwab-shaped chains. SPY, QQQ
  and CHEAP all show both sides; option lists remain populated, relative IV explains missing history, System
  reports source/quote coverage, and the browser reports no page errors or horizontal page overflow.
  Reproduce with `python -m tests.browser.stocks_fixture_server --scenario stale-prices --port 8879`, then
  `CHROMIUM_EXECUTABLE=/usr/bin/chromium node tests/browser/stocks_options_stale.mjs` with Playwright installed.
- All of this is functional/synthetic acceptance, not genuine forward trading or authenticated-provider validation.

## Install on the existing laptop checkout

```bash
cd ~/kestrel &&
git fetch origin codex/arbitrage-v1:refs/remotes/origin/codex/arbitrage-v1 &&
git switch codex/arbitrage-v1 &&
git pull --ff-only origin codex/arbitrage-v1 &&
uv sync --frozen &&
uv run desk open --desk stocks --restart
```

Keep the dashboard open. Existing Pause remains respected. If explicitly paused, Start PAPER resumes the
already-authorized collection/PAPER work. No database reset, manual ticker import or new paid API is required.
See Options and System after the first configured collection pass, especially the SPY/QQQ contract counts,
parser_version 2 and separate stock/options session dates. Do not claim the laptop was updated until it is.
