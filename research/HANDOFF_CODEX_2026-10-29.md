# Handoff for the next agent (Codex) — 2026-10-29

Branch: `claude/funny-rubin-gq99vj`. Pull first (`git fetch origin claude/funny-rubin-gq99vj && git pull`).
Read `docs/STOCKS_PLAYS.md` and `PROGRESS.md` for context. Rules that always hold: research and PAPER only (no
brokerage orders, no LIVE changes), no paid APIs, no AI/LLM judging trades, keys only in
`~/.config/desk/secrets.env` (never in the repo, logs, fixtures or the browser), Python 3.12 + uv, Ruff + strict mypy,
Prettier 3.3.3 for `src/desk/stocks/static/*` and `tests/browser/*.mjs`.

## Codex continuation — steps 0–3 completed

The original handoff below is retained as the work specification. Starting revision `89a2c40` passed the full
**1,466-test offline suite** before implementation (5 opt-in live tests deselected), Ruff and strict mypy.
The two pre-existing browser smoke formatting failures were corrected with pinned Prettier 3.3.3.

- **Step 1:** market-only Trending v2, independent WSB Home tab, visible counts/change/reported upvotes/source time,
  immutable supplemental list facts (`stocks_research/0012_list_details.sql`), and no relabeling of the old Trending
  specification. Qualified options use the existing sampled midpoint-premium activity measure, not invented trade
  prints or a false volume-versus-normal ratio.
- **Step 2:** stock replay statistical grades use the same forward reporter and tested-family correction, saved with
  each run's specification, full session count and date range, including in its retained archive. Forward and historical
  grades have separate UI cards/strategy summaries. Old specifications are stale; unsupported histories are explicit;
  replay version 3 refreshes old automatic runs when enabled. Forward counters, ledgers and promotion remain separate.
- **Step 3:** source/setup/method/PAPER docs and PROGRESS updated. Removed the stock page's unverified borrow-rate `%`.
- **Final checks:** **1,475 offline tests passed, 5 opt-in live tests deselected**, with the existing upstream
  Starlette/httpx deprecation warning. Ruff, strict mypy (246 files), JS syntax and pinned Prettier 3.3.3 pass.
  Required desktop/375px browser workflows pass, with PAPER replay run alone.

**Step 4 still needs the laptop's real credentials.** No authenticated provider responses were available here.
Do not mark synthetic/documented response fields verified. Preserve the original acceptance instructions below.

## What was finished in this session (all pushed)

| Commit | What |
|---|---|
| `d0c9055` | Design system: `static/stocks.css` holds every token (true black, neutral surfaces, mint `--up` / orange-red `--down`, lime `--accent`), Inter bundled (`static/inter.woff2`, OFL in `inter-OFL.txt`), pill controls, sticky header. research/plays/paper CSS use tokens only. |
| `10f935c` | Stock page data: migration `stocks_market/0007_symbol_feeds.sql`; `symbol_feeds.py` (Massive details/news/icon, Wikidata, Wikipedia, optional Finnhub, cached per source, background task in `web.py` lifespan); `stock_page.py` builds `GET /api/stocks/stock/{symbol}/profile`; Schwab `quote_details()` (extra quote sections, falls back on HTTP 400); `GET /api/stocks/lists/{id}`; committee reasons name only voting desks. Evidence: `research/evidence/2026-10-29/stock_page_sources.json`. |
| `b2efe4d` | Stock page UI: `static/stock_page.js` + `static/stock_page.css`, `KestrelResearch.stock()` in `research.js` rebuilt (hero, 1D/1W/1M/3M/YTD/1Y/5Y/Max chart, About, Key statistics, lists, news, ratings, earnings, financials, short interest, insiders, dividends, options chain, desks, peers, committee card). Finnhub key field in Settings. `UI_PROTOCOL = 7`. Fixture scenario `stock-page` + `tests/browser/stocks_stock_page.mjs`. |
| `194a932` | "Find the evidence" list page (`discover()` in `stocks.js`) rebuilt on `/api/stocks/lists/{id}`. |

Tests run and passing at handoff: `tests/unit/test_stocks_stock_page.py` (22), stock-related unit suites (114),
`tests/integration/test_stocks_analysis.py`, `test_stocks_play_history.py`, browser `stocks_smoke`, `stocks_gui_workflows`,
`stocks_intraday_workflows`, `stocks_options_stale`, `stocks_plays`, `stocks_plays_history`, `stocks_stock_page`,
`stocks_paper_workflows` (this one is timing-sensitive under CPU load: 3 of 4 runs passed; rerun alone).
At the original handoff, the full suite had not been run; Codex completed it as recorded above.

## Step 0 — verify the tree

```bash
uv sync
uv run ruff check src tests && uv run mypy src
uv run pytest -q                      # full suite; fix anything red before new work
npx --yes prettier@3.3.3 --check src/desk/stocks/static tests/browser
```

Browser tests (Playwright; see `tests/browser/README.md`): start a fixture server per scenario, e.g.
`uv run python -m tests.browser.stocks_fixture_server --scenario stock-page --port 8881` then
`KESTREL_STOCKS_URL=http://127.0.0.1:8881 node tests/browser/stocks_stock_page.mjs`.
Re-run at least `stocks_smoke.mjs` (populated), `stocks_stock_page.mjs` (stock-page), `stocks_intraday_workflows.mjs`
(intraday-workflows), `stocks_gui_workflows.mjs` (ui-workflows), `stocks_paper_workflows.mjs` (paper-workflows).

## Step 1 — Home tabs: Trending from market data, WallStreetBets in its own tab (user request)

User's words: "on the homepage trending should not be based on wall streetbets its not the most important data for
profit make a dedicated wsb tab next to something in that group of four Trending / Rising trends / Top gainers /
Top losers".

Where:
- `src/desk/stocks/lists.py`: `trending(day)` (around line 85) scores `crowd 50% + relative volume 30% + |1-day move|
  20%`, and its `ListSpec("trending", "Trending today", "What retail is talking about and trading", ...)` (around
  line 345). `wsb_vs_reality` already exists separately.
- `src/desk/stocks/static/research.js` home tabs (around line 381): `["trending", "Social attention"]`,
  `["momentum", "Rising trends"]`, `["gainers", "Top gainers"]`, `["losers", ...]`, and titles around line 401.

Do:
1. Change `trending` to market activity only, no crowd input: e.g. relative volume (vs 20-day median) 45%, absolute
   1-day move scaled by the stock's own 20-day volatility 35%, options activity 20% when the options desk has data
   (use `day.ctx.options` / the options desk facts already used by `unusual_options`). Require rel volume ≥ 1.5 or a
   ≥ 2σ move. Reason text lists those numbers ("volume 3.1× normal; +6.2% today (2.4σ); options volume 2.0× normal").
   Retitle the spec "Trending today" / blurb "Unusual volume and moves today, from market data only". Changing a list
   rule changes its `spec_sha256` — that is intended (new specification); do not rewrite old snapshots.
2. Add a new list `ListSpec("wsb", "WallStreetBets", "Most-mentioned on r/wallstreetbets in the last 24 hours", ...)`
   built from `day.ctx.crowd[s]["wsb"]` (mentions, rank, change vs 24h ago) — the crowd desk's existing inputs.
   Register it in `LISTS`/`BY_ID` like the others; add a unit/integration test like
   `test_lists_pick_the_planted_names_and_are_appended_once` (SQZ is the planted WSB name in `tests/stocks_world.py`).
3. Home tabs: `Trending` (market data), `Rising trends`, `Top gainers`, `Top losers`, `WallStreetBets` — keep
   "Market & options" and "Options activity" as they are. Update the tab titles map. Keep the existing
   "Activity, not direction" style badges.
4. Update `tests/browser/stocks_smoke.mjs` if it asserts the old "Social attention" text; run it.

## Step 2 — Paper portfolios: historical replay grade beside the forward grade (user request)

User's question: why must the 126/63-session evidence grade wait for future sessions when we have history? Answer to
give the user (and to put in the UI note): forward sessions stay the only basis of the *evidence grade* because a
replay is run by rules that were designed after seeing that history (selection and look-ahead bias) and Massive
Basic only has 2 years. But a replay of the exact same strategy code over stored point-in-time history is useful and
should be shown **separately**, clearly labelled.

Where:
- `src/desk/stocks/paper/report.py` `grade(acct)` (line ~101): `MIN_SESSIONS_PORTFOLIO` / `MIN_SESSIONS`,
  returns "Too early: N of 126/63 sessions".
- Historical replay already exists: `src/desk/stocks/backtest_job.py` (`BacktestWorker`, sessions 0/63/126/252),
  `replay_archive.py`, `static/replay.js`, Paper page "Explore strategies before today… Ready to replay 443 eligible
  sessions".

Do:
1. After a completed replay run for a strategy, compute the same statistics `report.py` computes for forward
   accounts (return, drawdown, vs baseline/SPY with the same Holm correction) over the replayed sessions, using the
   same `grade()` thresholds, and store it with the run (strategy id, spec sha, first/last replayed day, sessions).
2. In the Paper portfolios table (`static/paper.js`, the "Evidence grade" column) show two lines:
   `Forward: Too early · 5 of 126 sessions` and `Historical replay: <grade> · 443 sessions (Oct 2024–Oct 2026)`.
   If no replay has run: a "Run replay" link to the existing replay screen. Never merge replay sessions into the
   forward count; never let a replay grade unlock anything that the forward grade gates.
3. Grade replays only with the strategy's current specification (`spec_sha256`); mark older runs as stale.
4. Tests: unit test that a replay grade is computed from replay sessions and the forward grade is unchanged;
   browser assertion in `stocks_paper_workflows.mjs` that both lines render.

## Step 3 — documentation

- `PROGRESS.md`: add the four commits above plus steps 1–2.
- `docs/STOCKS_SOURCES.md`: add Massive ticker details/news/branding, Wikidata, Wikipedia summary and Finnhub
  (free tier per swagger.json: recommendation free, earnings last 4 quarters, calendar 1 month history; price target is
  premium and unused), with the facts from `research/evidence/2026-10-29/stock_page_sources.json`.
- `docs/SCHWAB.md`: the quote request now asks for `quote,fundamental,reference,extended,regular`; the field names in
  the extra sections are unverified until a real response is recorded (the stock page's "Company data sources" list
  shows which sections arrived).

## Step 4 — operator verification on the laptop (needs real keys; cannot be done in CI)

1. Open a stock (e.g. AAPL). Within ~30 s "Company data sources" should list Schwab sections received. Record one
   real `/quotes?fields=quote,fundamental,reference,extended,regular` response shape (values redacted) as a fixture
   and mark the verified fields in `QUOTE_DETAIL_FIELDS` (`src/desk/stocks/sources/schwab.py`). In particular
   confirm whether `divYield` is a percent and what `htbRate` units are before showing them.
2. Massive details/news/icon should fill within a minute (5 calls/62 s shared budget). Record a real ticker-details
   and news response (key stripped) and switch the registry endpoints to `status = "verified"`.
3. If the user adds `FINNHUB_API_KEY` (Settings → step 4), ratings/earnings/calendar should fill; record responses,
   mark verified.
4. Wikidata was rate-limited to 1 request/min on 2026-10-29 (their outage); the registry budget is 3/min. Confirm it
   fills over a few minutes.

## Known gaps / ideas not started

- Robinhood's "Hedge funds" trading trend needs SEC 13F holdings (quarterly); not collected. Insiders + short sellers
  are shown instead.
- No company logo for ETFs/without Massive key (monogram shown instead).
- `stocks_paper_workflows.mjs` flakes under heavy parallel load (replay playback races the Pause click).
