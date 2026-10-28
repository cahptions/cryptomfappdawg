# PROGRESS.md — Session-to-session state

> Read this first when resuming work in a new Claude Code session. Keep it current: milestone status, decisions,
> known bugs, measured results, and the exact next step.

## 2026-10-28 — History retains every identified play, without user clicks

Operator reported yesterday's History showing only two SPY/QQQ LEAPS rather than all recommendations. Root cause:
the old page queried only `stk_plays`, the first immutable after-close evaluation cohort. Intraday picks could leave
the closing ranks; categories frozen empty could not later acquire more rows. Clicking did not cause inclusion,
but the page gave no distinction between all discoveries, the closing snapshot and personal selections.

- History now defaults to **All identified plays**, combining the automatic idea archive and legacy closing picks,
  deduplicating exact structures per category and identification day. **Closing snapshot** remains selectable;
  coverage explains total saved, valued, waiting and closing counts. Personal confirmations remain separate.
- Recover up to 500 original saved first sightings automatically at startup and per tracking pass. Preserve
  original entry/legs/time, leave unavailable ranks/odds missing, and audit unreadable records once without blocking
  other recovery. No rebuilding old predictions with current data. New York identification dates prevent a stale
  quote session from backdating a newly saved idea.
- Every unexpired archived leg remains in quote rotation after leaving the board, independently of manual clicks.
  Reuse bounded daily repair/settlement and split quarantine under an archive mark namespace. Never turn intraday
  first-sighting quotes into closing marks or value an archive before identification. Closing cohorts and grading
  remain immutable and independent; unmatched twin subsets are not presented as an all-archive comparison.
- Archived rows link to dedicated P/L pages. Original quote anchors and later actual observations show progress;
  source gaps remain explicit. Recovered profiles disclose missing original model context and disable IV scenarios
  if the original underlying price was not saved. History polls while visible and preserves scroll/input editing.
- Corrected closing-history holding duration when the latest observation is an intraday quote on a later session.

Validation: six new regression tests reproduce the two-closing-pick failure, disappearing/late picks, zero manual
selections, quote rotation, automatic legacy recovery/idempotence, New York dates, expiry marking, no early closing
marks and malformed-sighting isolation. Forty-one targeted regression tests pass; lint, strict mypy over 240 source
files and desktop/375px archive and existing History/Performance browser checks pass. **1,427 offline tests passed,
5 opt-in live tests deselected**. Final live-state/coverage wording corrections passed 26 targeted tests and the
desktop/phone archive browser check again. The current-quote state says Open even without a first daily mark.

Limits: recovery requires original saved sightings; never-saved recommendations, model odds or option paths cannot
be recreated honestly. Live authenticated laptop state is not accessible here; the screenshot also lacked the
previous release's My tracked plays tab, so the current build needs updating and restarting on the laptop.

## 2026-10-27 — Personal play tracking, exact calculators, profit targets and company context

Continued from `d6c86d8` on the existing `claude/funny-rubin-gq99vj` release branch. All requested workflow additions
are implemented; the automatic Plays cohorts, strategy PAPER books, Learning journal and real brokerage boundary
remain separate.

- BUY/SELL badges open an adjacent, keyboard-accessible confirmation with quantity. **My tracked plays** lists
  confirmed selections, entry status and detail links. Stable idea IDs retain exact contracts even as ranks move.
  Confirmations are idempotent; ideas, selections and lifecycle events are append-only and survive restart.
- The operator clarified **market open, not a fifteen-minute delay**: premarket selections become eligible at
  9:30 New York; during-session confirmations need a subsequent quote, never a backdated opening fill. The next
  shared tracking pass captures fresh timestamped bid/ask references only. Entry/cancellation decisions serialize
  transactionally. Stale, future, missing-time, crossed and premarket quotes cannot enter a selection. No validated
  learned timing override exists, so the opening-session default is active. No brokerage orders are sent.
- Dedicated idea/personal pages show the retained pre-expiry P/L path and best observed exit, exact contracts,
  cancel/close controls, original analysis and an exact-leg calculator. It preserves call/put, strikes, expiry,
  sides and personal quantity, including spread legs. Frozen idea quotes survive loss of raw chain history; other
  strikes use the same saved session. Scenarios explicitly use the original idea snapshot, separately from actual
  personal-entry P/L. Active pages poll saved observations while preserving chart controls and inspected time.
- Cards put stock price beside ticker, label each Greek separately and explain Delta/Gamma/Theta/Vega with correct
  100-share dollar examples and per-day/per-IV-point units. Profit and +50%-on-risk-at-expiry meters are separate
  from ITM; +25%/+100% targets and finite-spread maximum-gain odds have expandable explanations. Analytic payoff-region
  probabilities are model estimates, not calibrated early-exit target-hit rates. Long-call/put theoretical maxima
  receive meaningful explanations rather than invented maximum-gain chances.
- Stock profiles automatically queue SEC company metadata and a sourced annual business excerpt in a separate,
  bounded, cached task. About shows available industry/sector, headquarters, phone, website, fiscal year-end,
  provenance and GUI refresh/settings links. Failures preserve the previous description and back off; unsupported
  annual layouts remain explicitly missing. Fund context stays distinct. No new paid data API is required.
- Browser checks found and fixed a scenario-input step mismatch on fractional-cent underlying prices. Integration
  checks caught the need to restrict profile serialization to display fields rather than compressed company facts.

Validation: **1,421 offline tests passed, 5 opt-in live tests deselected**. Ruff, strict mypy (239 source files), JS
syntax and Prettier checks pass.
Desktop/375px browser workflows pass: adjacent confirmation, exact put and multi-leg calculator, quantity, fresh
entry, fresh exit, restart/reload persistence, active chart-update control preservation and company context. Existing
Plays filters/cards/table and History/Performance checks pass at both widths. All fixtures are explicitly synthetic;
no operator credentials, private data or brokerage orders were used.

Limits: quote references are not depth-aware fills; provider budgets/cadence and the running dashboard determine
capture time. The calendar assumes a 16:00 close (half days remain unsupported). No hindsight-optimal sale or
profitable timing model is claimed. Real connection freshness and company filing extraction on the laptop still
depend on the configured providers; older missing option paths are never invented.

## 2026-10-27 — Earlier exit opportunities on the Plays P/L chart

Reviewed the latest published Stocks work at `a87feec` on `claude/funny-rubin-gq99vj`, which includes all of
`codex/arbitrage-v1` plus Plays, quote tracking, history, performance and learning. This change addresses the operator's
request to see what an option could have sold for before expiry, even when it eventually expired worthless.

- All/1M now preserve the available intraday path instead of discarding it for daily closes. The month ends at the
  play's last observation, and the time scale/cursor use actual timestamps. Dollar P/L is the default.
- Selected exit, best observed pre-expiry exit and the latest/expiry comparison have separate cards. The best has
  its timestamp, percentage, dollar amount, chart marker and jump button. Contract quantity scales dollars on every
  leg and preserves the inspected date. Pointer/touch inspection has a rectangular tooltip.
- Best observed uses bid/ask liquidation values after entry and fees. A different entry time applies to first-shown
  and close bases. Pre-entry, future, modelled/estimated/provisional and settlement points cannot win. Spread paths
  reject leg combinations more than five minutes apart. A losing best exit remains negative.
- Estimated gaps are optional, dashed amber, and excluded from the default chart scale and best observed result.
  Hidden estimates leave real gaps, not an invented bridge. Estimated-only data has a usable explicit state and
  overlay control. Retention/quote coverage and hindsight limitations are explained in expandable calculation notes.
- The expiry comparison describes a hypothetical hold, not an actual exercise/assignment or the only exit outcome.

Validation: **1,398 offline tests passed, 5 opt-in live tests deselected**; Ruff and strict mypy over 236 source files
passed. Desktop/375px browser checks cover the real multi-session fixture plus an explicitly synthetic quoted profit
spike followed by a worthless expiry, a higher excluded estimate, quantity/date preservation, range density,
tooltip, quote-only gaps and estimated-only recovery. No private laptop data, brokerage credentials or orders used.
Three baseline dashboard-start checks initially timed out under concurrent browser/suite load; all 11 startup tests
passed independently and the subsequent full offline run passed without those failures.

Limits: the best retained quote is not the true market-wide optimum or a guaranteed executable fill. Missing option
quotes remain estimates; after intraday retention expires the older path has only daily marks. This presentation
does not train on hindsight-optimal exits or establish a profitable strategy. Authenticated laptop freshness and
sustained forward acceptance remain to be checked on the actual running installation.

## 2026-10-27 — Plays history: every pick tracked, marked, charted, graded and learned from

Built from `research/plays/HISTORY_BUILD_PROMPT.md` in five pushed steps on `claude/funny-rubin-gq99vj`
(operator request: "tomorrow I click Yesterday and see what yesterday's picks are doing", live via Schwab, gaps
repopulated on open, P/L chart from the pick to expiry, and the app learning from it).

- **Live tracking** (`play_tracking.py`, migration `0007`): every leg of unexpired recorded plays, today's board and
  twins, quoted in five-minute passes via Schwab `/quotes` (100 option symbols a request, padded roots,
  identity/multiplier/underlying checks, Schwab IV and greeks kept), chain fallback, free collector chains (only
  ones observed by the pass time), ≤ 40 requests a pass with priorities and a closing pass from 15:50. Fold at close
  into packed intraday (≥ 30 days, trigger-enforced) and permanent daily closes with KESTREL's and Schwab's greeks.
  First sightings recorded during the session. System panel; `doctor --online` probes option quotes.
- **Marks and repair** (`play_marks.py`, migration `0008`): one append-only mark per play and twin per session —
  quoted, modelled, estimated (laptop-off gaps: real stock closes, IV interpolated between bracketing quotes, minute
  paths where Schwab still has them, unreliable flags for results/4σ moves), settled exactly (matches the expiry
  grade), or provisional until bracketed. Splits/halts quarantined; early-assignment warnings. Twins: seeded random
  matched contracts on other stocks.
- **History view and P/L charts** (`play_history.py`, `static/plays_history.js`): quick buttons and a date picker,
  per-session rows with live P/L, peak/low, status and sparklines, summaries with "$100 each" and twins; per-play chart
  (1D/1W intraday, 1M/All daily, %/$, close vs first-shown basis, crosshair, markers, dashed estimates), greeks then vs
  now, reasons and marks. UI protocol 5 → 6.
- **Performance** (`play_performance.py`): saved after the close; holding periods, categories by rule version, DTE and
  price buckets vs twins, calibration chart, cumulative $100 curves vs twins and SPY. Validated chart palette.
- **Learning** (`play_learning.py`, migration `0009`): calibration per category and DTE (current probability model
  only), walk-forward exit rules, input ICs, five preregistered challengers recorded in shadow and graded at expiry,
  promotion only after 20 later sessions with a session block bootstrap + Holm, failed challengers kept, proven/failing
  labels on the board and in Performance.
- Bugs found and fixed on the way: collector chains stamped after the pass time leaked into ticks (point-in-time);
  one empty quote answer disabled the endpoint for the day; a finish cache keyed by `id()` reused ids across
  categories.
- Tests: tracking (14), marks and repair (11), learning (9), multi-session integration on `tests/stocks_world.py`
  with a laptop-off session and an expiry (9), browser `tests/browser/stocks_plays_history.mjs` (fixture scenario
  `plays-history`) at 1366/375 px, plus the existing Plays checks. Measured: fold of 2,000 contracts ≈ 2 s, marks pass
  over 3,000 plays ≈ 1.7 s (0.1 s idle), ~0.7 KB per contract-session intraday, 136 B per daily close, 112 B per mark.

Unproven until the laptop runs it: Schwab's quotes endpoint serving option symbols to the approved app (`doctor
--online` → `plays_option_quotes`), real quote freshness under the shared budget, and every learning result (it needs
weeks of settled plays: labels need 60 settled over 20 sessions, promotions 20 later sessions). Next: watch the first
week on the laptop; consider a PAPER strategy that follows a category once it is proven.

## 2026-10-27 — Plays: the options of the day

Branch `claude/funny-rubin-gq99vj` was fast-forwarded to `codex/arbitrage-v1` (`1398d8c`, 1,337 tests passing) and
then gained **Plays** (operator request): a Stocks tab ranking option ideas in twelve categories — most likely to
finish ITM, biggest model edge, unusual activity, cheap volatility, earnings, trend calls, bearish puts, squeeze
calls, sell rich premium (credit spreads only), lottos, LEAPS and the watchlist. Each play shows buy/sell, type,
strike, expiry, stock price, bid/ask/spread, volume/OI, IV and rank, net greeks, cost or credit, worst/best case,
break-even, expected value and three chances in the money (market-implied, the stock's own history, and KESTREL's
forecast, which is tilted only by the committee's measured IC and calibrated on graded plays).

- `odds.py`: closed-form and history probabilities, touch probability, expected payoff, IC-bounded tilt and
  calibration; checked against simulation (6 tests).
- `plays.py`: vectorized IV/greeks over every stored contract (~0.3 s for 1,440), category selectors, unusual-activity
  scoring on eight quote-snapshot signals with next-session open-interest confirmation, once-per-session recording
  after the close (`stk_play_days`, `stk_plays`) and once-only grading at expiry (`stk_play_outcomes`), all
  append-only (migration `stocks_research/0006_plays.sql`). Built in `research_preview` after each options pass,
  skipped when its inputs are unchanged, saved to `stocks-plays.json`; a failure is shown on the page and never stops
  collection. Session = newest stored chain day.
- Collection now also fetches the expiry nearest a year out (≥ 180 days) for LEAPS.
- `GET /api/stocks/plays`; UI protocol 4 → 5 (page and server must match; restart once after updating).
- Tests: 6 odds unit tests, 6 Plays integration tests (consistency of prices/odds/greeks, planted unusual burst,
  next-session confirmation, record once, grade at expiry, endpoint), browser check `tests/browser/stocks_plays.mjs`
  passed populated and empty fixtures at 1366 px and 375 px with no console errors or horizontal scroll.

Limits: quote snapshots, not exchange prints (direction is a labelled inference); coverage is the collector's rotating
batches; greeks are computed from mids without dividends; recorded entries use end-of-session quotes; KESTREL's
chance is uncalibrated until 30 plays resolve and untilted until the committee has a positive measured IC. Docs:
[docs/STOCKS_PLAYS.md](docs/STOCKS_PLAYS.md). Next: watch the first recorded sessions and gradings on the laptop;
consider keeping Schwab's own greeks/IV in the chain parser (currently discarded) to cross-check the computed ones.

## 2026-10-21 — Stocks continuation and optional Schwab market data

**2026-10-22 UTC reliability campaign:** see `research/reliability/HANDOFF.md` and its fault registry/results.
Final full offline suite: **1,123 passed, 5 deselected**; Ruff, strict mypy208files and UI build passed.
Empty/populated Stocks browser workflows and five readiness states passed at1366px/375px. New protections cover
input/resource limits, cache revisions, point-in-time corporate actions/options, partial receipts, transaction
failures, managed-worker restart/Pause and sanitized bounded diagnostics (`desk stocks diagnose`). No operator
data/processes/providers were used. User-requested **Stocks-only PAPER** event sizing v2:15%desired (squeeze7.5%),
20%account cap,2%planned stop-risk budget and0.5%daily-dollar-volume cap, checked at decision/fill. Insider default
effectively10%because of its20%stop. Crypto and options limits/capital remain separate. Old-spec cumulative results
cannot grade the revised strategy. Intraday cadence, current fee schedules, laptop power recovery and real forward
performance remain unverified. Automatic Stocks PAPER stays authorized; one-time update/backend restart required.
Screenshot follow-up reproduced two more barriers: latest unpublished close blocked existing research, and
previews waited behind optional feeds. Previews now use honestly dated latest published prices before network
work and after price batches. Exactly100-session integration coverage populates home/lists without PAPER writes;
pending collection cards no longer claim zero qualifying stocks. See fault R15. Arbitrage was requested as a
separate future desk: the copy-ready research/build prompt is saved in `research/arbitrage/BUILD_PROMPT.md`.

**Automatic PAPER requested and implemented:** opening the Stocks dashboard now starts its combined PAPER and
collection worker automatically unless the user has explicitly paused it. It runs continuously, refreshes mutable
research previews, and processes ready forward PAPER sessions without requiring a second terminal command.
Pause is persisted; normal shutdown preserves the startup preference. A lifespan monitor waits for any external
Stocks worker to release its lock before starting; GET never spawns or duplicates work. Unexpected exits remain
visible without restart loops. First-session warmup now defers stepping rather than freezing an incomplete
history date. PAPER network waits cancel safely before unlock; synchronous ledger transactions remain atomic.
No capital, strategy entry/fill timing, STOP or LIVE authorization changes. Validation:124 offline Stocks tests,
Ruff/mypy and empty/populated desktop/phone browser controls/readiness passed. Actual laptop activation remains
an operator update/restart; no remote laptop process was started here.

**Stocks menu application:** added the missing Stocks desktop entry/icon to launcher installation. A selected-only
`desk open --desk stocks --install` creates its menu shortcut and opens the research dashboard without installing
services or launching collectors/engines. It preserves other desks' launchers; configured/custom ports persist in
the shortcut. Stocks gets a separate lime icon/profile/class and initially uses the full screen work area.
Validation:53 relevant launcher/CLI/service tests, Ruff/mypy, and all three generated desktop entries validated.

**Collector crash identified and corrected:** the operator's research log reported `ZeroDivisionError` in
`fundamentals.scores`, cost-of-revenue / prior annual revenue. The old code was reproduced failing with synthetic
zero-revenue statements in both cost and reported-gross-profit paths. Both years now use guarded margin ratios;
an incomplete Piotroski score stays unavailable rather than counting unknown components as failures. Beneish
requires positive revenue and defined component ratios rather than neutral1 fallbacks. Invalid/non-finite ratio
results stay missing; valid independent metrics can still be used. This can reduce score coverage for incomplete
statements. Offline Stocks validation:115 tests passed, Ruff clean, strict mypy38files clean. A research-pipeline
regression confirms lists are produced with a zero-revenue company and the PAPER ledger is unchanged. Laptop
recovery still requires pulling the fix and starting research again; existing downloaded data remains saved.
The traceback passed the history guard, so the earlier20-session report no longer measures current progress.

**Home readiness correction:** unrecorded lists now show history download counts, research collection status,
and pending-analysis guidance instead of claiming no qualifying names or asking an already configured user to
connect data. Only a dated, empty list receives the no-matches message. A dedicated Chromium check covered
unconfigured,20-session running/stopped,60-session pending, and recorded-empty states at1366px/375px.
This is presentation only: historical warmup, liquidity filters, collection and order behavior are unchanged.

**Operator market-data acceptance:** the laptop's `desk stocks doctor --online` returned Schwab
`connected=true`, `quote_received=true`, `options_provider=schwab`, `problems={}`, and integrity=ok/WAL for
all three Stocks databases. Quote timestamp: 2026-10-21 17:43:06.135 UTC. Orders remained disabled. This
confirms this quote/login path, not full option-chain coverage, refresh over time or prospective performance.
Listings, Massive bars/actions, SEC companies, mentions, Treasury rates and short volume also had successful
jobs. Twenty price sessions were stored; the minimum60 observations per eligible stock still need backfill.
The initial corporate-actions history took1488.3seconds and completed; it is not a repeated full download.
Two optional feeds failed. FINRA's exact JSON error was reproduced with a CSV header beginning
`accountingYearMonthNumber`; requests now explicitly ask for JSON and the parser also accepts validated CSV,
keeps missing values missing, rejects non-finite/wrong-settlement records and paginates by received rows.
FRED explicitly asks for CSV and validates the requested series/date schema. Non-data/HTML responses remain
unavailable rather than becoming measurements. The operator's subsequent public curl probe returned an HTML
page titled **Site Maintenance | St. Louis Fed**, confirming a source-maintenance response for the VIX CSV
endpoint at that time. No FRED credential/configuration change is indicated; its next scheduled rates job can
retry. This probe does not establish when maintenance ends or separately verify the credit-spread endpoint.
The CSV test fixture is a serialization variant of recorded JSON rows, not a new live source capture.
Validation: 31 relevant offline source/collection tests, Ruff and strict mypy passed. No running laptop
collector was changed here; the free rate ceilings and trading behavior remain unchanged.

**Schwab setup recovery:** operator hit the ambiguous "stop the Stocks worker" instruction, stopped the
dashboard instead, and then hit an expired browser CSRF token. Settings now distinguishes the dashboard
from collection, presents an owned-collector pause button beside the Schwab fields, disables saving while
collection is active, preserves typed fields across pause/status updates, and shows individual saved key/secret
presence without returning values. Only an explicit stale-session 403 renews the token and retries once;
other 403s and network failures are not automatically retried. Research-only collection now interrupts stalled
network/rate-limit waits on pause, awaiting cleanup before releasing the worker lock. PAPER shutdown semantics
are preserved. Failed OAuth attempts can be cancelled before correcting credentials; cancellation must close
the callback thread before a replacement save. Authorization requires Schwab selected/saved, and identical
Client ID/Client Secret values are rejected before any write. The operator's portal screenshot confirms
registration of https://127.0.0.1:8182/oauth/callback with Ready For Use status for their app. This is portal
acceptance, not an authenticated data or OAuth-completion check. No operator process was stopped from here.
Validation: 35 relevant offline credential/dashboard/PAPER checks, Ruff, strict mypy, and both empty/populated
desktop/phone browser flows passed. Browser cases explicitly exercise pause with typed inputs preserved,
one stale-session retry, no retry of other 403s, saved presence, and cancel/retry of authorization. Broker
credentials and successful authenticated market data remain unverified on the operator's laptop.

**Empty-dashboard/onboarding correction:** Settings now accepts the free Massive key, SEC contact email and
optional Tradier token directly, with signup links and required/optional explanations; no manual secrets-file
editing is needed. Blanks preserve credentials, unrelated providers/comments survive, private permissions are
enforced, and auxiliary saves do not invalidate Schwab tokens. Start/Pause controls explicitly launch only a
dashboard-owned, bounded research-only collector (`desk stocks collect`); no worker starts on GET and a
terminal-owned worker cannot be signalled from this UI. Collection reads keys again on each pass. Nasdaq
listings survive optional SEC mapping errors; adding SEC contact triggers mapping retry on the next pass.
Home refreshes on stored price/job revisions while a pass runs. Research previews can recompute on the same
session as backfill arrives without writing PAPER or frozen evidence. Existing frozen records are preserved.
Public reference/attention/short/rates feeds are wired, but do not replace the required price key. The mockup
used example data; the operator still needs to create their own free price account. Validation: 1,018 offline
tests (5 live deselected), Ruff/strict mypy, and isolated desktop/phone empty/populated browser checks passed.
No real finance endpoint was probed in this pass: the managed HTTP policy currently excludes those hosts,
and the operator's credentials/data remain on their laptop. Authenticated access, full-universe performance
and prospective evidence still need laptop observations. Start instructions are in [docs/STOCKS.md](docs/STOCKS.md).

**Research hub correction:** operator clarified that Home must be a polished stock/options discovery workspace,
not a portfolio summary or a dropdown-only list browser. Default landing is now the Market overview, with
benchmark ETF strip, Trending/Rising trends/Gainers/Losers top10 tabs, six research collections, three options
collections, breadth, sectors, estimated catalysts and a watchlist. The mockup's stock-page structure is reflected
in the chart/metrics/evidence/chain layout and right-hand committee. Options has a dedicated overview.
`desk open --desk stocks` is wired without starting a trading worker. All data comes from stored research;
there are no sample-market fallbacks or new trading signals. Split adjustment/gap handling, bounded option
tables and CSRF-protected watch updates have regression checks. Validation: 1,009 offline tests, Ruff, strict
mypy, and populated/empty desktop/phone browser flows passed. Browser fixtures are synthetic and isolated
from operator data. Restart an old dashboard after pulling, before opening the new home.

Branch `codex/finish-schwab-stocks` continues Claude's saved M1–M6 through `972565b`, including the earlier
Robinhood/Solana research and $500 reference changes. Added `desk stocks doctor/connect/refresh/paper/report/
validate/learn/dashboard`, a loopback dashboard on8767, eleven-desk stock pages, PAPER records, evidence and
private Settings. It implements no brokerage account/order endpoint and does not arm LIVE.

Schwab credentials/tokens stay owner-only beside secrets.env. Token POSTs bypass raw-response storage;
refresh is serialized and rotating tokens are atomically persisted. Exact HTTPS callbacks and random OAuth
state are checked. A self-signed local HTTPS listener on8182 needs no paid domain. Tailscale Serve is optional
for a `.ts.net` callback if needed; no VPN, public Funnel or operator machine was configured by this session.
The accessible SDK still documents `https://127.0.0.1`; the official portal was proxy-denied, so a blanket
2024 ban was not verified. See [Schwab evidence/guide](docs/SCHWAB.md).

PAPER now commits a whole ledger session atomically, obeys STOP, and refuses stock openings and option
snapshots from before order creation. Missed-session recovery is recorded as simulation; it discovers no
retrospective entries. Chain provenance is recorded and one asset/session cannot mix sources. Collection
uses bounded batches; below1.5GB available RAM the worker defers broad analysis. The dashboard reads stored
data and performs no research backtest on a request. Tradier remains the default until explicitly changed.

Validation: 1,007 offline tests passed (5 live tests deselected); Ruff and strict mypy passed. Chromium smoke
passed empty/populated desktop and375px layouts, chart, eleven desks, PAPER/evidence/system/settings and CSP.
OAuth/provider fixtures are synthetic; loopback HTTPS is actually exercised. No authenticated broker probe,
real credentials, funding or brokerage order was used. Follow-up checks are recorded in the continuation guide.

Unknown/outstanding: actual Schwab app approval/callback acceptance/entitlements/live schema, full-universe
laptop RAM/disk/time measurements and prospective evidence. Options coverage is a deliberately sparse pilot;
dated fee assumptions, modeled marks/expiry and survivor bias prevent executable-edge/LIVE claims. Stocks
$10k/$50k experiments are distinct from $500 crypto reference and $100k learning cash. Start/update commands
and the durable handoff are [docs/STOCKS.md](docs/STOCKS.md); earlier milestone notes below remain history.

## Current status

| Milestone | Status | Date | Notes |
|---|---|---|---|
| **M0** Architecture & feasibility | ✅ **Approved by operator** | 2026-10-18 | PLAN.md, RESEARCH.md, ACCEPTANCE.md, provider registry draft, evidence |
| **M1** Infrastructure & data collection | ✅ **PASS** | 2026-10-18 Eastern / 2026-10-19 UTC | laptop doctor and 60-minute collection reported; 139 offline tests, ruff and strict mypy rechecked on resumption |
| M2 Discovery & on-chain safety | 🟡 **implemented; 24-hour acceptance pending** | 2026-10-18 Eastern / Oct 19 UTC | automated checks and short cloud discovery runs passed; see M2 results and M2_HANDOFF.md |
| M3 Features & strategy engine | 🟡 implemented; forward acceptance pending | 2026-10-19 | measured context, SHADOW and replay; six-hour laptop run still needed |
| ~~M4 AI judge~~ | ❌ **Dropped** | 2026-10-18 | Operator declined paid API; desk is deterministic-only (PLAN §15) |
| M5 Full PAPER trading | 🟡 implemented; 72-hour acceptance pending | 2026-10-19 | $100,000 learning book, $500 reference replica; measured fees; H8 moonshot with evidence-gated conviction sizing |
| M6 Statistical learning & confidence | 🟡 methods implemented; forward evidence pending | 2026-10-20 | confidence matrices, counterfactuals, champion review, zero-influence local models, record-only evidence council |
| M7 Dashboard & operations | 🟡 implementation and automated checks | 2026-10-19 | React/FastAPI, Paper trading tab, services, backups; real-laptop acceptance pending |
| M8 LIVE infrastructure | 🟡 implemented; tested against fakes only | 2026-10-19 | signer re-checks all seven gates per BUY; LIVE engine and guardian book from measured balances; no wallet funded |
| M9 Forward validation | 🟡 report tooling implemented; forward evidence pending | 2026-10-19 | `desk report readiness`, 6-hourly refresh while PAPER runs; ≥ 60 days calendar time still required |

**Mode of the system:** the configured default is SHADOW. Free provider keys remain private on the operator's
laptop. PAPER uses a simulated **$100,000 learning book**, tickets based on a **$500 initial allocation plus
realized paper profit**, and a separate conservative $500-account replica. LIVE trading is implemented (a signer
that re-checks all seven gates for every BUY, a LIVE engine and an independent LIVE guardian) but stays impossible
until you set `desk.mode = "LIVE"`, `DESK_LIVE_CONFIRM=yes` and an allocation, approve a version the readiness
report calls ELIGIBLE, and arm the signer. No wallet has been funded. See the latest continuation below.

## M0 acceptance results (2026-10-18)

| ID | Result |
|---|---|
| M0-01 | Delivered; operator approved 2026-10-18 |
| M0-02 | All external facts in RESEARCH.md carry evidence tags |
| M0-03 | 20 sanitized evidence files in `research/evidence/2026-10-18/` (cookies stripped; secret-pattern scan: only a false positive inside a token's marketing text) |
| M0-04 | Questions batched in PLAN.md §24 |
| M0-05 | No implementation code written |

## M1 acceptance results (2026-10-18, cloud container — see "Where" column)

Commands: `uv run pytest` (offline suite), `uv run pytest -m live` (read-only network), `uv run ruff check src tests`,
`uv run mypy`, `uv run desk doctor`, `uv run desk collect --minutes 60`.
Totals at commit time: **139 offline tests passed, 4 live tests passed, ruff clean, mypy --strict clean (46 files).**

| ID | Result | Where | Evidence |
|---|---|---|---|
| M1-01 Reproducible install | ✅ PASS | cloud (fresh `git clone` + `uv sync --frozen`) | 289 hashes in `uv.lock`; `desk 0.1.0`; same config snapshot `52014c14933b`; 135/135 tests in the clean clone |
| M1-02 `desk doctor` on the laptop | ✅ **PASS** (2026-10-19 UTC, operator's laptop) | keyed rerun: **19 ok / 1 warn / 0 fail** | see "Operator laptop doctor" below |
| M1-03 Config hash | ✅ PASS | unit | `tests/unit/test_config.py` |
| M1-04 Ceilings | ✅ PASS | unit | ceiling + floor + paid-provider tests |
| M1-05 Real fixtures parse | ✅ PASS | unit | 15 real responses, 0 validation errors |
| M1-06 Unknown is never zero | ✅ PASS | unit + DB | DexScreener bonding-curve liquidity → MISSING; DB CHECK constraint |
| M1-07 Schema drift rejected | ✅ PASS | unit + mocked HTTP | truncated JSON, wrong shapes, NaN, per-item errors, typed `SCHEMA` errors recorded in health |
| M1-08 Units | ✅ PASS | property tests | decimals 0–12, lamports, 70-digit provider decimals |
| M1-09 Timestamps | ✅ PASS | unit | ISO `Z`, offsets, epoch s/ms, nanoseconds, s/ms mix-ups rejected |
| M1-10 Budget never exceeded | ✅ PASS | simulated 10 min × 3 tasks + **3 real OS processes** | no window ever over budget |
| M1-11 Backoff on 429 | ✅ PASS | unit + mocked HTTP | `retry-after: 0` ignored; 2→4→8 s; capped; per-provider isolation |
| M1-12 Cache + single-flight | ✅ PASS | unit + mocked HTTP | 10 concurrent → 1 upstream call; expired entries dropped |
| M1-13 Databases | ✅ PASS | unit + fault injection | 4 DBs from empty, WAL, integrity ok, migration tamper detection, **kill -9 lost 0 committed rows** |
| M1-14 Secret redaction | ✅ PASS | unit | keys absent from message/args/extra/exception text and JSON log file |
| M1-15 Provider health | ✅ PASS | unit | OK→DEGRADED(3)→DOWN(10)→OK(3 successes), transitions timestamped |
| M1-16 Provider registry | ✅ PASS | unit | `config/providers.toml` schema, buckets, evidence files, every adapter has an entry |
| M1-17 Raw evidence | ✅ PASS | unit | byte-identical by SHA-256; corruption detected; retention keeps decision-linked |
| M1-18 60-min collection | ✅ **PASS — laptop results supplied by operator** | laptop 2026-10-19 00:01–01:01 UTC (Oct 18, 8:01–9:01 p.m. Eastern) | see "Operator laptop 60-minute run" below; previous cloud evidence retained separately |
| M1-19 On-chain constants | ✅ PASS | live (cloud) | Token, Token-2022, wSOL, USDC, venue programs vs live pools |
| M1-20 No paid / LLM deps | ✅ PASS | unit | no LLM SDK in lockfile, no LLM hosts in source, all providers $0 |

### Operator laptop doctor (M1-02, 2026-10-19 ~00:00 UTC)
Same config snapshot as tested (`52014c14933b`) → the laptop runs the exact verified configuration.

| Check | Laptop result |
|---|---|
| Python | 3.12.3 (system, Mint 22.3) |
| Disk | 274 GB free for `~/desk-data` |
| Memory / swap | 9.6 GB available of 15.0 GB; **swap 1456/2047 MB (71%) — warning** |
| NTP / clock vs chain | synchronized / +1.7 s |
| Power | BAT1 full, 100% |
| Keys | none yet (keyless mode) |
| GeckoTerminal | 20 new pools, 241 ms, 0 schema problems |
| DexScreener | 148 ms |
| Jupiter quote | 0.1 SOL → 10.4002 USDC (SOL ≈ $104.00), metis, 2 bps, 317 ms |
| Jupiter tokens (keyless lite-api) | 2/2 in 181 ms |
| Solana public RPC | slot 458626262 in 401 ms; Token-2022 mint parsed in 117 ms |
| **Public RPC `getTokenLargestAccounts`** | **429 rate_limited from the home IP too** → R-01 resolved: a keyed RPC is REQUIRED for holder analysis (M2) |

Laptop latencies are lower than the cloud container's on every provider.

**Doctor re-run with the free keys configured (2026-10-19): 19 ok / 1 warn (swap 64%) / 0 fail.**
Secrets file private (0600); Jupiter key and Helius RPC URL configured (values never recorded). Jupiter tokens via
`api.jup.ag` (keyed) 2/2 in 41 ms; keyed RPC slot in 155 ms; Token-2022 mint in 38 ms;
**`getTokenLargestAccounts` returned 20 accounts in 85 ms** — holder analysis for M2 is unblocked. Market migration
0002 applied automatically on the laptop database.

### Operator laptop 60-minute run (M1-18)

Evidence source: the operator pasted the terminal summary in this chat. This is an operator-reported laptop run,
not a new cloud execution. Run ID: `run_01M58QDRAS0EG93KBSJYMDSG4E`.
Started `2026-10-19T00:01:47.865649+00:00`; ended `2026-10-19T01:01:47.489277+00:00`.
Local time: **October 18, 8:01–9:01 p.m. America/New_York**.

| Job | Polls | OK | Failed | Success | Items | Item errors |
|---|---|---|---|---|---|---|
| geckoterminal_new_pools | 60 | 60 | 0 | 100% | 2,400 | 0 |
| geckoterminal_trending | 30 | 30 | 0 | 100% | 600 | 0 |
| jupiter_recent | 60 | 60 | 0 | 100% | 1,800 | 0 |
| jupiter_trending | 20 | 20 | 0 | 100% | 1,000 | 0 |
| dexscreener_refresh | 60 | 60 | 0 | 100% | 9,494 | 0 |
| rpc_slot | 60 | 60 | 0 | 100% | 60 | 0 |

Unhandled exceptions **0**; suspend gaps **0**; tracked tokens **261**; memory **50.9 MB current and peak**.
Reported database totals: tokens **1,159**, pools **1,215**, market snapshots **12,494**, token claims **2,800**,
quotes **0**, mint accounts **0**, raw responses **601**, ingest errors **0**. These are database totals, not
asserted run-only deltas. Zero quote/mint rows are expected for M1 collection and do not validate M2 security or
execution. M1-18's poll-success, exception and <500 MB memory thresholds are met by the supplied report.

### Cloud 60-minute run (M1-18, cloud container, 2026-10-18 23:03 → 2026-10-19 00:03 UTC)
| Job | Polls | OK | Failed | Success | Items | Item errors |
|---|---|---|---|---|---|---|
| geckoterminal_new_pools | 60 | 60 | 0 | 100% | 2,400 | 0 |
| geckoterminal_trending | 30 | 30 | 0 | 100% | 600 | 0 |
| jupiter_recent | 60 | 60 | 0 | 100% | 1,800 | 0 |
| jupiter_trending | 20 | 20 | 0 | 100% | 1,000 | 0 |
| dexscreener_refresh | 60 | 60 | 0 | 100% | 10,250 | 0 |
| rpc_slot | 60 | 60 | 0 | 100% | 60 | 0 |

Unhandled exceptions **0** · suspend gaps 0 · tracked tokens 269 · **memory 59.5 MB (peak)** (run used the cache code
from before the eviction fix) · rows: 13,250 snapshots, 1,139 tokens, 1,185 pools, 2,800 token claims, 617 raw
responses · disk: 23 MB DB + 6.9 MB raw per hour · `integrity_check = ok`.
24 HTTP 429s were absorbed by backoff; health transitions recorded and self-recovered: GeckoTerminal
ok→degraded→ok (23:33→23:37), Jupiter ok→degraded→ok (23:58→00:00).

**Finding from the run — drained pools:** 602 snapshots (22 PumpSwap pools among ~269 tracked tokens) had
DexScreener `liquidity.usd = 0` *explicitly*, with reserves like 34 tokens / 0.0000217 SOL while 24 h volume was still
shown (~$9k). These are drained pools (a rug signature). The values were stored truthfully but unflagged, unlike
GeckoTerminal zeros. Fixed: both providers now flag `ZERO_REPORTED` / `ZERO_LIQUIDITY_REPORTED`, and DexScreener
reserve amounts are stored (`market` migration 0002). Real response saved as evidence + fixture
(`research/evidence/2026-10-19/m1_dexscreener_tokens_v1_drained_pumpswap_pools.json`). Input for M2 rug detection.

### Defects found and fixed during M1 (honest log)
1. **Out-of-range epoch timestamps** raised a raw `ValueError` instead of the typed `TimestampError` — caught by a unit test; fixed.
2. **All providers shared one rate-limit bucket** (every endpoint used the bucket name `default`), so a GeckoTerminal
   429 backoff would also have stalled DexScreener/Jupiter/RPC. Found by inspecting `runtime.db` during the first live
   60-minute run (which was stopped and restarted on the fixed code). Regression test added and shown to fail without
   the fix.
3. **Rate-limiter wait timeout** surfaced as a bare `TimeoutError` instead of `ProviderError(RATE_LIMITED)` — fixed + test.
4. **Response cache kept expired entries** until 2,048 entries (up to ~60 MB on long runs) — now dropped promptly + test.
6. **DexScreener explicit-zero liquidity was not flagged** (GeckoTerminal zeros were) — found in the 60-minute run; fixed,
   reserve amounts now stored (migration 0002), upgrade-from-v1 migration test added.
5. My own analysis error: I reported GeckoTerminal `pools/multi` as returning "3 of 4" pools; the request actually
   contained a duplicate address. Corrected in the registry and RESEARCH.md.

## M2 implementation and acceptance status (2026-10-19 UTC / October 18 Eastern)

Resumed from Claude's latest pushed commit `df4866d`. The Claude session URL was inaccessible from this
environment; the repository's PLAN, ACCEPTANCE and PROGRESS documents supplied the handoff. User instruction:
finish M1 and continue through the remaining milestones. Free keys were already verified on the laptop.

Implemented discovery identities/source observations, independent timestamps, resumable backfill, stages 0–4,
mint/extension policy, conservative PumpSwap/PDA/vault/LP verification, lower-bound holder metrics, Shield and
fresh exact-quantity quote checks, append-only stage evidence, fair bounded queues, request-allocation totals,
and a local read-only console. No wallet, funds, LLM or paid service was used.

| Criteria | Status | Evidence |
|---|---|---|
| M2-01–04 Identity, source dedup, timestamps and reasons | PASS (automated) | tests/unit/test_discovery.py; migration detection and all six reason fixtures; ordinary refresh is not renewed activity |
| M2-05–06 Authorities/extensions | PASS (automated) | tests/unit/test_security.py; real Token-2022 fixture and unsafe/unknown extension cases; missing/empty authority fields fail closed |
| M2-07–09 Holders, attribution and claim separation | PASS (automated; synthetic proof sets) | concentration/exclusion arithmetic, owner aggregation, provider conflict and cluster contract tests; complete live holder resolution still requires the laptop keyed run |
| M2-10–11 Routes, costs and missing evidence | PASS (automated) | stale/mismatched quantity quotes, unavailable routes, cost cap, missing required evidence and Shield tests |
| M2-12–13 Audit completeness/provenance | PASS in tests and short runs; long-run evidence pending | atomic rollback, append-only SQL triggers, zero orphaned candidates in cloud smoke reports; every result has rule/values/thresholds/hash |
| M2-14 Actual 24-hour discovery | **PENDING** | 2-minute / 2-minute / 1-minute cloud runs are smoke tests only; the duration gate correctly refuses the 1-minute run |
| M2-15 Scores cannot override security | PASS (automated) | score is not accepted as a policy input; storage refuses security PASS without a matching assessment |

Validation: **212 offline tests passed**, **4 live provider tests passed**, Ruff clean, strict mypy clean
(58 source files), frozen dependency install, wheel build, database integrity/WAL checks, and functional startup
of the CLI console with an HTTP status request. Offline tests include real SQLite/HTTP boundaries with mocked
provider responses; they do not substitute for live keyed safety evidence.

Actual cloud discovery runs are saved in `research/evidence/2026-10-19/m2_cloud_smoke_runs.json`. The latter
two runs reported **57.3 MB** and **57.6 MB** peak memory, zero unhandled exceptions, zero ingest errors and
zero orphaned candidates. Final cumulative candidate count: **573**. Source and outcome totals are cumulative;
the final report also separates `during_run` events. These are **cloud**, not laptop, measurements.

Public RPC holder queries hit HTTP 429 during the first run; candidates stayed DEFER, never PASS. Later
iterations rejected known unsupported pool features before spending further holder/route requests. A real
PumpSwap account and a real Shield response were saved byte-for-byte alongside provenance records. Current
special PumpSwap features and all unverified venues remain excluded; see M2_HANDOFF.md for exact limits.

Cloud setup draft saved: corrected repository SHA/mount path, tested frozen install script, startup instructions
and the six required provider domains. Draft persistence is confirmed; review/save/publication and fresh-task
restoration have **not** been claimed. The draft does not contain credential values.

## M3 development and conditional sizing (2026-10-19 UTC)

Operator clarified that development should proceed while the 24-hour run finishes. Later software can be built
in parallel; acceptance criteria and calendar-time evidence remain unchanged. User also requested position sizes
that grow only when realized profit and confidence support the increase, with a hard cap relative to account size.

Implemented the point-in-time feature registry/builder, versioned regime classifier, frozen strategy hypotheses,
NO_TRADE ranking, immutable strategy/input/SHADOW decision storage, bounded offline SHADOW runner and streamed
replay/threshold experiments. Added exact pool and quote provenance to new security dossiers; old dossiers retain
missing provenance. No additional runtime dependency or provider requests were added by M3.

Validation performed in the cloud: **376 offline tests passed, 4 live tests deselected** (14.11 seconds), Ruff
clean, strict mypy clean across **73 source files**. New coverage includes 41 feature/regime tests, 46 strategy
tests (including 1,000 randomized repeated decisions), 15 SHADOW/replay integration tests and 62 sizing tests.
Integration uses independent WAL connections for concurrent collector writes/SHADOW decisions and verifies
that PAPER ledger contents remain unchanged. These numbers are cloud results, not laptop benchmarks.

Actual CLI smoke `shadow_01M5921PFDR21T3RJHD4NYHV3Z`: 1.80 seconds, 2 cycles, **20 NO_TRADE decisions**, zero
unhandled exceptions, **37.1 MB peak RSS**. All **20/20** decisions replayed identically; the six-hour gate
correctly refused this short run. All four database integrity/WAL checks passed. Exact outputs are stored in
`research/evidence/2026-10-19/m3_cloud_smoke.json`. This smoke does not close M3's six-hour criterion.

Conditional sizing is a standalone **PAPER-only recommendation foundation**, not an execution engine or matrix
producer. Fixed fraction remains 10%; the hard ceiling is now **15% of eligible capital**, replacing the fixed
0.15 SOL default per the user's explicit instruction. Old/custom profiles may retain an optional absolute cap
as additional tightening. Eligible profit growth requires fresh matching strategy/regime PAPER evidence, all
required confidence dimensions, 300 trades/ESS150/60 days, positive conservative net/baseline/OOS bounds and no
deterioration/integrity issue. Larger targets require their own complete fresh quotes and positive net expectancy
at that size. Liquidity, deployable capital, reserve, exposure, drawdown and fee limits still bind. Unit tests
include eligible growth beyond 0.15 SOL, unqualified profits/equity exclusion and shrinking account equity.
Independent review also tested that caller policies cannot loosen configured quote-age/cost limits; effective
limits use the stricter policy/configuration value. Every recommendation hashes all sizing/risk/discovery/policy
inputs. Original replay integrity is checked even in alternative-threshold experiments.

**Still pending:** M2's actual 24-hour laptop acceptance; M3's actual six-hour SHADOW/replay acceptance; measured
global-context collection/reference integration; verified migration/spike/retest event chronology. Without
genuine thirty-day context the runner reports insufficient regime history and chooses NO_TRADE. Provider
claims do not silently become verified strategy inputs. M5 wallet/guardian/execution and M6 statistical-matrix
production are not implemented by this foundation. M7–M9 also remain pending. No real funds or LIVE capability.

Delivery branch: `codex/m3-strategy-engine`; see `M3_HANDOFF.md`. Let an already-running laptop test finish
before switching the code it is using. Publishing code does not update the laptop process automatically.

## M3 market-context collection (Claude, resumed 2026-10-19 UTC)

The other agent stopped after M3's software foundation (`4bc063e`). Its handoff listed "measured global-context
collection" as missing: the regime classifier needs fresh context plus a measured trailing 30-day reference, and no
code recorded either, so every SHADOW decision was NO_TRADE by construction and the 30-day clock could not start.
Operator confirmed on 2026-10-19 that the per-trade cap is meant to be a percentage of account capital (15%), with
no fixed SOL cap.

Implemented (versioned definition `regime/context.py`, `CONTEXT_VERSION`):
- `market_context` collector job every 60 s (both `desk collect` and `desk discover`), one append-only row per
  measurement in market migration 0005. Unknown inputs are NULL with a recorded reason, never 0.
  - SOL 1 h log return and realized volatility from GeckoTerminal 1-minute OHLCV of the Orca SOL/USDC pool
    `Czfq3x…u44zE` (verified live: wSOL/USDC, $28.7M reserve, $2.4M h1 volume; evidence
    `m3_geckoterminal_sol_usdc_reference_pools.json`, `m3_geckoterminal_ohlcv_sol_usdc_1m.json`). One extra GT
    request per minute (≤ 3.5/min total vs the 8/min self-limit).
  - Watchlist volume: sum of DexScreener h1 volume, latest row per pool within 180 s, ≥ 20 pools, refresh current.
  - Migrations/hour: distinct mints with a MIGRATION discovery observation in the last hour; only measured when
    `desk discover` has run continuously (no suspend) for that hour. `desk collect` records it as missing.
  - 24 h hazard: cohort = pools whose first positive DexScreener liquidity (≥ $5,000) was 24–25 h earlier; event =
    first observation ≥ 24 h later shows ≤ 20% of entry liquidity (explicit zero counts). Needs ≥ 20 resolved and
    ≥ 80% of the cohort. A `hazard_followup` job (every 5 min, ≈ 1 DexScreener call) re-observes cohort pools at
    24 h so tokens evicted from the 300-token refresh set don't look like survivors. Existing snapshots are
    back-filled into the cohort by the migration.
- Feature builder fills the five context features from the latest stored row (max age 120 s; stale/missing keep
  their reason). SHADOW builds the 30-day reference from stored rows only (one sample per hour, anchored at the
  decision hour, cached per hour), so the regime stays MISSING until 30 measured days exist.

Validation (cloud): **391 offline tests passed** (15 new in `tests/unit/test_market_context.py`: hand-computed SOL
metrics on the recorded response, stale/future/missing-anchor/gap rejection, watchlist window, migration window,
hazard events incl. explicit zero, early/future outcomes and coverage, follow-up selection, cohort back-fill on
upgrade, append-only rows, 30-day/gap/point-in-time reference, feature staleness, collector job incl. provider
failure), Ruff clean, strict mypy clean (74 files). Live 3-minute cloud smoke `run_01M5952S2SBVRDYZPRQRNRKVYN`:
all 8 jobs 100%, 0 unhandled exceptions, 59.4 MB peak; 3 context rows with SOL return/volatility recorded,
watchlist volume measured on the third row (67 pools), hazard correctly missing (no 24 h history), migrations
correctly missing under `collect`; 66 cohort entries. Evidence: `m3_context_cloud_smoke.json`. Cloud, not laptop.

**Operator action:** after the current 24-hour M2 run finishes, update to this branch and keep `desk discover`
running as continuously as practical. Regime labels (and therefore any non-NO_TRADE SHADOW signal) need 30 days of
measured context with no gap longer than a day; there is no shortcut.

## Operator decisions for PAPER learning (2026-10-19, Claude session)

Recorded in PLAN §13.1.1 and §16.5 gate 7:
1. Don't wait 30 days to learn. Research/PAPER decisions now proceed with an **uncalibrated** regime (recorded);
   only a calibrated label can exclude a strategy. Evaluator bumped to `shadow-rules-v2`; replay refuses runs
   recorded by a different evaluator version instead of reporting them as integrity failures. The catastrophic
   rule (SOL ≤ −8% in 1 h ⇒ no entries) applies from day one. LIVE requires a calibrated regime.
2. PAPER cash **$100,000**, trade sizes as the **$100 account** (10% of $100 + realized net profit; grows as a
   percentage of profit immediately; never below the $100-account size).
3. Many concurrent PAPER positions, limited by the free quote budget instead of the real account's 2.
4. A separate $100-account replica reports what the real account would have done with the same signals.

Tests after (1): **394 passed**, Ruff and strict mypy clean. Items (2)–(4) are implemented in M5.

## M5 PAPER learning book (2026-10-19 UTC, Claude)

Implemented per PLAN §12, §13.1.1, §13.3, §13.4: ledger migration 0002 (positions with a one-open-per-mint unique
index and unique intent ids, append-only cash journal and audit events, marks), pure rules (`paper/model.py`: fee
model, observed/conservative/stress profiles, deterministic simulated failures, learning-book sizing, the eight exit
policies, impairment), quote-only executor (buy → sell-back cost check → wait → second buy, worse quote used),
entry engine hooked to discovery's security PASS (decides while the exact-ticket quotes are fresh), an independent
guardian process (`desk guardian`, auto-started by `desk paper`, own provider clients, marks every 25 s, exits
every 5 s loop, EXIT_BLOCKED after 3 failed exits with lock retained, re-quotes after suspend/resume), kill switch
(`desk stop` / typed `desk resume`), and `desk paper-report` with reconciliation and the $100-account replica.
Strategy specs v2 (B1, B2, H1–H6) allow PAPER; R1 stays SHADOW-only.

| Criteria | Status | Evidence |
|---|---|---|
| M5-01 reconciliation | PASS (automated) | journal vs independently recomputed cash, 0 lamport difference in every lifecycle test |
| M5-02/03/21 sizing, fee floor | PASS (automated) | `tests/unit/test_paper_model.py` table tests incl. exact 1% boundary, growth with profit, no shrink on loss |
| M5-04 quote-only fills | PASS (automated) | lifecycle asserts the exact quote calls; the executor has no price input |
| M5-05 three profiles | PASS (automated) | observed ≥ conservative ≥ stress by construction and per-trade results |
| M5-07 failure handling | PASS (automated) | no route ⇒ ENTRY_FAILED, no position; failed exits ⇒ EXIT_BLOCKED, never closed |
| M5-08 liquidity collapse / impairment | PASS (rules) | impairment and liquidity-exit boundary tests; no-route > 5 min ⇒ IMPAIRED |
| M5-09 exit policies | PASS (automated) | 18-row boundary table + invalidation test |
| M5-10 locks / duplicates | PASS (automated) | gate refusal and database unique index |
| M5-11 restart recovery | PASS (automated, guardian) | a new guardian resumes an EXIT_BLOCKED position from the ledger and closes it |
| M5-12 guardian independence | PASS (fault injection) | real processes: engine SIGKILLed mid-write ⇒ no partial position or orphan cash entry, integrity ok; the guardian process keeps marking and exits every position (HARD_STOP); entries refused once its heartbeat is stale |
| M5-13 kill switch | PASS (automated) | STOP blocks the next entry check; `desk resume` needs typed RESUME |
| M5-14/22 limits, replica | PASS (automated) | learning-book gates in lifecycle tests; replica rewritten as one open/close timeline with 10 edge tests: 2-position limit, same-instant close frees the slot, open positions hold slots, no compounding above $100, losses shrink tickets, daily loss limit (incl. a loss closed after midnight counting for the new day — a bug in the first version), trades/day, permanent cumulative-drawdown halt, fee floor |
| M5-15 P&L by hand | PASS (automated) | trailing-stop trade checked to the lamport |
| M5-17 mode separation | PASS (automated) | no LIVE ledger, no SHADOW rows written |
| M5-18 sleep/resume | PASS (fault injection) | a 30-min wall-clock jump makes the guardian re-quote every position before deciding (mutation-checked: removing the re-quote fails the test); a collector gap blocks PAPER entries for 5 min |
| M5-19 lifecycle | PASS (fake provider) | decision → entry → marks → trailing exit → reconciliation → report, every step in the audit trail |
| M5-20 72-hour PAPER run | **PENDING (laptop)** | needs the keyed laptop configuration |

Validation (cloud): **445 offline tests passed** (incl. 3 PAPER fault-injection and 10 replica tests), Ruff clean, strict mypy clean (82 files). Live 3-minute cloud
smoke `desk paper` (`m5_paper_cloud_smoke.json`): book created from a real 1 SOL quote (105.16 USD/SOL ⇒ 950.95 SOL
cash, 0.951 SOL sizing base), all 11 jobs 100%, 0 unhandled exceptions, 63.7 MB peak, guardian spawned as its own
process with a 0.5 s-old heartbeat and exited 0 on SIGTERM, reconciliation difference 0. No trades in the cloud:
keyless public RPC cannot return holder lists (R-01), so no candidate passes security there. The smoke found two
real bugs, both fixed with tests: USD→lamport conversion at a live rate, and the guardian idling out before the
engine's first heartbeat.

## M6 statistics and confidence matrix (2026-10-19 UTC, Claude)

`desk confidence` builds PLAN §14.1's matrix per strategy version from the PAPER ledger and gives the §14.3
readiness state; every report is stored append-only in the research registry with an inputs hash. Cells carry
estimate, bounds, evidence count and status; no weighted score exists and no model output enters. Methods
(`stats/core.py`, numpy 2.3.4 added, pinned): Politis–Romano stationary bootstrap with Politis–White block length
(chunked to stay laptop-sized), Geyer ESS on hour-clustered trades, drawdown with bootstrap p95, ES95, Wilson,
lower CUSUM (k = 0.12, h = 20) and Page–Hinkley, Holm, Brier/ECE, walk-forward folds with purge + embargo, and a
point-in-time leakage audit. Baselines (B0–B2) are reported but never promoted. "ELIGIBLE FOR MANUAL LIVE REVIEW"
only permits your review; it never arms LIVE.

| Criteria | Status | Actual result |
|---|---|---|
| M6-01 bootstrap coverage | PASS | 500 AR(1) φ=0.2 Student-t(3) series, n=300: **93.6%** coverage of the true mean |
| M6-02 walk-forward purge/embargo | PASS | no training label window reaches an embargo before the test; embargo < max hold refused |
| M6-03 leakage detector | PASS | a deliberately leaky "latest value" feature is flagged; the real FeatureBuilder is clean |
| M6-04 calibration metrics | PASS | Brier and ECE match hand computation |
| M6-05 counterfactual tracking | PASS (automated) | `counterfactuals` job (every 5 min under discover/paper): first REJECT of every candidate gets +5m/15m/1h/6h/24h outcomes from same-pool observations, re-fetching due pools; unobservable pools are kept as NO_DATA (no survivorship); a deterministic 10% sample gets a round-trip quote check at 1 h. `desk counterfactuals` summarises |
| M6-06 no-edge never promoted | PASS | 1,000 zero-mean heavy-tailed datasets: expectancy passes **4.7%** (≤ 7%); ELIGIBLE **0** (67 checked incl. controls) |
| M6-07 real edge detected | PASS | +4%, σ 25%, n 600, 90 days: expectancy + reliability pass in **98%** of 200 runs |
| M6-08 deterioration | PASS | +4% → −2% at trade 300: ≤ 10% false alarms before the change, ≥ 80% flagged within 300 trades (expected delay ≈ 176) |
| M6-09 matrix cells | PASS | all 14 dimensions present with estimate/bounds/count/status; readiness from rules only |
| M6-10 registry append-only | PASS | report stored; delete refused |
| M6-11 champion/challenger | PASS (automated) | `desk champion evaluate/approve`: own expectancy + reliability PASS and Holm-adjusted p < 0.05 vs the champion, then typed `PROMOTE <version>`; approvals and history append-only; a stale comparison is refused |
| M6-12 Holm | PASS | matches reference |
| M6-13/14 local models | PASS (automated) | L2 logistic model (`desk model score/report`) scores every recorded decision using only labels known before it (≥ 100 trades, refit every 25), logs model version, training window, feature-set version and inputs hash; out-of-sample benchmark vs B0/B1/B2 with Brier skill and ECE. Zero influence: decision hash identical whether the model says 0, 1 or fails; no trading package imports `desk.models` (AST test); `research.model_influence` accepts only "none" |

Validation: **478 offline tests passed** after the M6 completion, Ruff clean, strict mypy clean (92 files). The first CUSUM setting
(h = 12) gave 35% false alarms on healthy data — consistent with its theoretical run length — so h was raised
to 20 and the documented detection delay updated rather than loosening the test.

## M8 LIVE infrastructure — zero funds (2026-10-19 UTC, Claude)

`desk_signer` (`src/desk/signer/`) is the only holder of the hot-wallet key: scrypt + AES-256-GCM keystore at
`~/.config/desk/hot-wallet.json` (0600), unlocked only inside `desk live arm`, serving a 0600 Unix socket that
accepts four structured operations (status, intent, withdraw, disarm). Every swap intent goes order (Jupiter
`/swap/v2/order` with the hot wallet as taker) → inspection → balance-delta simulation → signature → `/execute` →
signature polling until `lastValidBlockHeight`; refusals are final and journaled (synchronous=FULL). `desk live
status` shows the seven §16.5 gates (calibrated regime added); `desk live approve <version>` records a typed
approval; `desk live validate` runs the zero-cost path; `desk liquidate --all [--live]` stops entries and attempts
every exit. Added dependency: `cryptography` 46.0.3 (pinned). `live.max_capital_sol = 0` by default: LIVE is
impossible until you set it (ceiling 2 SOL) and every other gate passes.

| Criteria | Status | Evidence |
|---|---|---|
| M8-01 key isolation | PASS | AST test: no module outside `desk.signer` imports the keystore; non-0600 keystore refused |
| M8-02 encrypted keystore | PASS | wrong passphrase refused; file holds no key bytes (hex/base58/base64 checked); nothing logged (log capture) |
| M8-03 intent-only | PASS | raw-transaction ops, unknown fields, string/float amounts, expired or far-future intents, amounts above the 15%-of-allocation cap and a zero allocation are refused before any order |
| M8-04 inspection | PASS | 9 crafted attacks refused (foreign program, SOL to a stranger, wrong fee payer, output to a foreign account, token transfer, priority fee too high, two routes, close to a stranger, over-wrapping) + order/blockhash mismatches; the **real recorded Jupiter order passes** |
| M8-05 simulation required | PASS | simulation failure, overspend or under-delivery ⇒ no signature (journal has none) |
| M8-06 gate matrix | **PASS (continuation)** | first found INCOMPLETE: the tests covered a display evaluator disconnected from signer BUY. `desk live arm` now builds the signer with the production `LiveAuthorizer`; with all seven gates the real signer orders, inspects, simulates, signs once and confirms; removing any single gate refuses before any order or signature (`tests/unit/test_live_authorize.py`) |
| M8-07 no blind resubmit | PASS | execute timeout ⇒ polling only; one submission; EXPIRED after lastValidBlockHeight, or CONFIRMED if it lands |
| M8-08 idempotency & recovery | PASS | duplicate id never re-signed; restart resolves SIGNED intents by polling and refuses RECEIVED ones (crashed before signing); no new intent while one is unresolved |
| M8-09 zero-cost path | **PASS (live, cloud)** | `desk live validate`: real order for a public funded wallet passed inspection; simulation err = null, 76,169 CU; nothing signed or sent (`m8_zero_cost_validation.json`) |
| M8-10 emergency halt | PASS (fakes) | STOP first; every exit runs the LIVE guardian's own path (wallet balance sold, sale booked from measured balances, account closed); per-position report CLOSED / SOLD / UNRESOLVED / FAILED with its reason; failures stay open and supervised; ledger reconciles |
| M8-11 no real money moved | PASS | no hot wallet was funded; no transaction was ever sent |
| M8-12 restricted withdrawal | PASS | only to the registered address with the exact phrase; single System transfer verified in the signed bytes |

Measured fees (R-06 data): the two live orders reported priority fees of **461** and **12,113 lamports** and a
5,000-lamport signature fee — far below PAPER's conservative 100,000-lamport model, which stays in place (a floor in
the ceilings) until enough measurements exist. Validation: **531 offline tests passed + 1 live**, Ruff clean, strict
mypy clean.

## Key measurements from M0 (details + evidence in RESEARCH.md)

- Jupiter quote round-trip cost on memecoins with $22k–$59k liquidity: **2.2%–4.1%** (0.1–1 SOL tickets).
- Jupiter platform fee: 10 bps/side on memecoins; 2 bps SOL→USDC (RFQ).
- GeckoTerminal: documented ~10 calls/min; measured ~50% 429s at ~35 attempted/min; 60 s cache.
- DexScreener: 300/min on pair/token endpoints, 30 addresses/call; `liquidity` absent on bonding-curve pairs.
- Public RPC: `getTokenLargestAccounts` 429 on 4/4 attempts (shared cloud IP — re-measure at home in M1).
- Public WS: pump.fun program logs ≈ 140–234 msg/s ≈ 19 GB/day.
- New pump.fun mints: Token-2022 with `metadataPointer` + `tokenMetadata` only.
- Token-account rent: 0.001488 SOL (165 B), 0.001514 SOL (170 B).
- SOL ≈ $101.8 at research time.

## Architectural decisions (proposed in M0; final once approved)

| ID | Decision | Rationale |
|---|---|---|
| D-01 | Four processes: engine, guardian, console, research (+ signer only in LIVE) | Failure-domain isolation; guardian owns all exits |
| D-02 | Three SQLite DBs: `market.db`, `ledger-paper.db` / `ledger-live.db`, `research.db` | Physical mode separation; small critical ledger backed up often |
| D-03 | Horizon 15 min–24 h; no sniping | Data cached 30–60 s; costs 2–4% |
| D-04 | DexScreener = refresh workhorse; GT = discovery + OHLCV; Jupiter = quotes + Tokens v2 + execution | Measured/documented limits |
| D-05 | Token-2022 extension allow-list (fail-closed) | pump.fun mints are Token-2022 |
| D-06 | `Observed[T]` wrapper; unknown never zero | Spec + DexScreener missing-field finding |
| D-07 | Pure decision core shared by SHADOW/PAPER/LIVE | Replay determinism; mode parity |
| D-08 | Conservative paper profile drives portfolio state | Avoid optimistic simulation |
| D-09 | **No AI judge, no LLM, no paid APIs** (operator decision 2026-10-18). "Models evaluate" = local statistical models in M6, zero influence until validated | Operator declined paying for an API; spec permits deterministic-only mode |
| D-10 | LIVE arming is CLI-only; dashboard can only disarm | Attack surface |
| D-11 | React + TS + Vite dashboard; UI built with Node or Docker; runtime Python-only | Polish + maintainability |
| D-12 | Internal package `desk`, brand only in UI (working brand KESTREL) | Brand can change freely |
| D-13 | Python 3.12 via `uv`, hash-locked | Matches operator's Mint 22.3; reproducible |
| D-14 | Address constants verified on-chain, never typed from memory | A memory-typed USDC mint was wrong during M0 |
| D-15 | Minimal dependencies: runtime M1 deps are only `pydantic` + `httpx`; stdlib `sqlite3` + numbered SQL migrations (not SQLAlchemy/Alembic), stdlib `logging` JSON formatter (not structlog), `tomllib` registry `config/providers.toml` (not YAML), `argparse` CLI | Fewer moving parts for a single beginner maintainer; plain SQL is readable |
| D-16 | Fourth SQLite file `runtime.db` for cross-process coordination (rate-limit windows, provider health, heartbeats) | Keeps transient coordination writes away from the market store and the money ledger |
| D-17 | $100 small-account sizing profile (10% tickets, max 2 positions, fee floor) | 2% tickets would be ~$2, where fees could be 0.25–5% per trade |

## Known issues / open items
See RESEARCH.md §11 (R-01 … R-10) and PLAN.md §23 (risk register).

## Operator environment (from operator, 2026-10-18)
Linux Mint 22.3 (Ubuntu 24.04), Ryzen 7 7730U, 16 GB RAM (swap 83% used at report time, ~6 Docker containers),
~290 GB free NVMe, Wi-Fi only (RTL8821CE), laptop on battery/AC, Tailscale installed, 1920×1080 display.

## Operator answers received
| Question | Answer | Date | Effect |
|---|---|---|---|
| Q1 Plan | **Approved** | 2026-10-18 | M1 started |
| Q2 Free keys | **Done** — free Jupiter API key + free Helius RPC configured on the laptop | 2026-10-19 | Verified by doctor (keyed Jupiter tokens, keyed RPC, holder list OK); keys live only in `~/.config/desk/secrets.env` |
| Q3 Capital | **$100** | 2026-10-18 | PLAN §13.2 rewritten as a $100 small-account profile: 10% tickets (~$10), max 2 positions, fee floor; paper simulates $100 |
| Q4 AI judge | **No** — "the AI shouldn't judge; I'm not paying for an API" | 2026-10-18 | M4 dropped; PLAN §15 rewritten; M1-20, M6-13, M6-14 added; running cost $0/month |
| Q5 Dashboard stack | **Approved** (React + TypeScript) | 2026-10-18 | — |
| Q6 Brand | **KESTREL** (inferred: "5 approve" covered the summary item that bundled stack + brand) | 2026-10-18 | changeable before M7 |
| Q8 Thresholds | Approved as part of plan approval | 2026-10-18 | research baseline |
| Q7 Telegram, Q9 uptime/wired | not answered — defaults apply | — | non-blocking |

## Next step
1. Finish an existing laptop acceptance run before switching its checkout. Preserve local changes.
2. Follow [CONTINUATION_HANDOFF.md](CONTINUATION_HANDOFF.md) to fetch `claude/funny-rubin-gq99vj` (it contains
   all of `codex/continue-claude`), install frozen dependencies, restart the workers and open `desk dashboard`; the
   checked-in UI needs no Node runtime.
3. Record actual M2 24-hour discovery, M3 six-hour SHADOW/replay and M5 72-hour PAPER acceptance separately.
   Do the dashboard, service, backup recovery, lid/power and memory checks on the Dell laptop.
4. M8 is implemented and tested against fakes; nothing has been funded. Continue M9 forward evidence for the
   required calendar duration and trade count. Real money only after the readiness verdict and your own decision,
   following docs/RUNBOOK.md ("Going LIVE"). M4 remains dropped. Do not promote a strategy from fixtures,
   synthetic tests, short browser checks or uncalibrated research results.

## Session log
- **2026-10-18 (session 1):** M0 research (live read-only probes of GeckoTerminal, DexScreener, Solana public RPC/WS,
  Jupiter v1/v2/Tokens/Shield; docs for Jupiter, Helius, Solana clusters, DexScreener, GeckoTerminal; Anthropic
  reference). Wrote PLAN.md, RESEARCH.md, ACCEPTANCE.md, PROGRESS.md, `research/provider_registry.yaml`, evidence.
  Incorporated operator's hardware report (PLAN §2, §18). Built a static visual concept
  (`docs/design/kestrel-concept.html`, sample data only); validated chart/text colours with the dataviz checker —
  muted text changed `#5F6D7D` → `#76869A` (contrast 3.6 → 5.1:1); brand neons restricted to identity use; series
  palette stepped into the dark band (worst adjacent CVD ΔE 7.9 ⇒ direct labels mandatory). Rendered at 1920×1080 and
  390 px: no console errors, no external requests, no horizontal page scroll. Stopped for approval.
- **2026-10-18 (session 1, later):** Operator answered Q4: no AI judge / no paid API. Removed the judge from PLAN
  (§1, §3.1, §4 diagram and pipeline, §5, §6, §7, §8, §9, §13, §15, §19, §20, §22, §24), dropped M4 in ACCEPTANCE
  (moved its surviving intents to M1-20, M6-13, M6-14, M7-04), marked Anthropic EXCLUDED in the registry and RESEARCH,
  removed the judge from the design concept. Still awaiting Q1 (plan approval).


## Claude continuation: M7 and backend fixes (2026-10-19 UTC, Codex)

Continued from Claude commit `c29501a` on `codex/continue-claude`. The current handoff is
[CONTINUATION_HANDOFF.md](CONTINUATION_HANDOFF.md); laptop operations are in [docs/RUNBOOK.md](docs/RUNBOOK.md).

M7 now includes a built React/TypeScript interface and a loopback FastAPI server. Eight views display recorded
book/replica results, candidates, positions, confidence, research, audit, resources and LIVE requirements.
Explicit controls start SHADOW/PAPER, stop entries, generate reports, export and disarm. Resume/arm/approval/
withdrawal are not dashboard operations. Host/Origin checks, session CSRF, CSP and inert text rendering protect
the browser boundary. Assets are local, dependencies locked, no paid service is introduced.

Overview history is bounded. PAPER summaries aggregate in SQLite; replica history is cursor-streamed with
bounded active-position state. It matches the original full report on profit/loss, concurrency, drawdown and
fixed-fee fixtures. A 12,000-event payload test retained under 1 MB peak Python allocation for the summary.
Historical scans still take time proportional to database history; laptop long-duration performance is pending.

PAPER rechecks STOP/halts/guardian/context after quote delays and before committing entries, serializes cash
settlement on close, retries blocked exits independently of mark refreshes, and prevents duplicate guardians.
New entry/dashboard/guardian/Telegram lifetime locks cover supervised startup and guarded restore.

User service units carry independent engine/guardian sleep inhibitors and memory caps. Hourly ledger/daily
market+research backups verify checksums, SQLite integrity and ledger mode, rotate, and preserve a pre-restore
copy. Position-state-change backup hooks remain unconnected. Telegram status/stop is optional and off by
default; tests injected HTTP and no external message was sent.

M8-06 was corrected above: previous tests exercised a disconnected seven-gate evaluator. Production signer
BUY now fails closed before ordering/signing; a real per-entry authorization producer is still missing. SELL
recovery and restricted withdrawal remain. No wallet was funded and no transaction signed or submitted here.

Browser evidence: production app with empty/populated offline fixtures in Chromium 154, all eight views,
dossiers, actual CSV/JSON downloads, STOP cancel/confirm, keyboard, 1920/1366/390 layouts, zero JavaScript/CSP/
request errors. One SSE remained through 24 view changes and closed after leaving; healthy SSE did not also
poll. A 12-second post-GC sample retained about 164–167 KiB. This is a brief regression check, not a long-term
leak test or a Dell/Firefox measurement. Reports and labelled fixture screenshots: [docs/evidence/m7/](docs/evidence/m7/).

Checks: strict TypeScript/Vite build, all seven foreground contrast tokens across four surfaces, zero npm audit
vulnerabilities, systemd unit syntax verification, Ruff and strict mypy (120 source files) passed. Cloud offline
doctor had zero failures (keyless isolated test home); this does not retest private laptop keys. Actual user
service startup, power/lid behavior, accessibility review and timed forward runs remain pending.

Final offline suite: **672 passed, 5 opt-in live tests deselected**, 147.89 seconds. One test-client deprecation
warning comes from Starlette's existing httpx adapter; it did not fail a check. No live provider check was rerun
or real transaction attempted during this continuation.

Subsequent requested chart addition: actual 5-minute OHLCV storage/backfill, candlestick/volume charts,
EMA20/50 and Wilder RSI14 in candidate/position dossiers. Bounded two-pool-per-minute collection shares the
existing free quota; it does not alter trade approvals. Chart ingestion/indicator tests passed (21), affected
storage/API/context/PAPER checks passed (45), Ruff and strict mypy (122 files) passed, and Chromium rendered
80 fixture candles, all three indicator paths and 4h/All controls. Synthetic chart screenshot is labelled under
docs/evidence/m7; short checks do not close laptop or timed acceptance.

Operator's latest report: run_01M590ZRP2PPM8G96ZZRXB08XR started 2026-10-19T02:48:55Z and was still active
at the supplied 17:21 UTC status. Providers had recovered to OK, ingest_errors=0, orphans=0; duration acceptance
was false because the run had not finished cleanly yet. Lid closure was reported; suspend gaps cannot be
determined from that report. Do not mark 24-hour acceptance complete or discard the captured data.

## Paper trading tab, realistic fees and the moonshot hypothesis (2026-10-19 UTC, Claude)

Continued on `claude/funny-rubin-gq99vj` after fast-forwarding Codex's `codex/continue-claude` (`32344da`), so the
branch now carries everything above. Operator requests: a tab showing what paper trading is doing; paper trades
must pay fees ("if not accurate then good estimates"); look for rare moonshots and size up only with great
confidence.

**Fees (PLAN §12.3, M5-23).** A recorded live comparison on one route shows Jupiter's Ultra `outAmount` already
equals the fee-free output × (1 − `feeBps`) to one raw unit, so the swap fee is never deducted twice
(`research/evidence/2026-10-19/m5_jupiter_fee_embedding.json`). Network costs are now three transactions per round
trip — buy, sell and closing the token account, which is how rent comes back; the close transaction was missing
before. Every 10 minutes the collector asks Jupiter for real orders built for a public funded address (nothing is
signed or sent) and stores the reported fees (`fee_observations`, market migration 0008). With six or more reports in
24 hours each transaction is charged the p90 priority fee × 1.5 plus the largest signature fee; otherwise the
conservative 105,000-lamport model. Measured priority fees so far: 332–12,113 lamports. Each trade records its fee
source and a breakdown (entry, sell, close, failed, rent locked/refunded). R-06 is resolved.

**H8 moonshot and ranking (PLAN §11.4, §13.1.1, M5-24).** H8 requires +50% in 1 h and still rising, accelerating
volume, heavy buying, liquidity +25%, top-10 ≤ 25% and developer ≤ 2%; it exits through a 15% stop, a trailing stop
that arms at +25% and gives back 12%, and a 2-hour limit. Conviction sizing: 1.5× the normal fraction (15% cap) only
with PASS net-expectancy evidence for H8's exact version in a confidence report at most 7 days old plus extra
volume and buy-pressure confirmation; otherwise normal size. The finding that mattered most: under the previous
single ranking B2's score scale (100 × log return) beat every hypothesis, so hypotheses could not earn PAPER
evidence. Evaluator `shadow-rules-v3` ranks an eligible hypothesis above any baseline; NO_TRADE still wins ties.
Existing strategy version hashes are unchanged (verified), so open positions keep their exact specifications.

**Replay across evaluator versions (M3-11).** Replay now reproduces a run with the evaluator that recorded it.
Runs recorded by the real v1 and v2 code (separate worktrees at `0b038bf` and `32344da`) replayed 5/5 identical
under the new code, including the uncalibrated and RISK_OFF cases where those versions differ. A six-hour SHADOW
session recorded before this update still counts.

**Paper trading tab (M7-14).** `/api/paper` plus a React tab (`g` then `t`): status banner with guardian/engine
heartbeats; cash, next ticket, realized P&L (observed/stress), open count and win rate; a plain-English activity
feed with filters (trades, signals, skipped, passed on); the signal-to-trade funnel for 24 h and all time; why
entries were skipped; open positions with exit ladder, hard stop, trailing stop and time left; closed trades after
fees; realized P&L curve; $100 replica; fees; moonshot watch; per-strategy and exit-reason tables. Reads are bounded:
the latest 100 trade/skip events plus 50 decisions, 200 closed trades and 400 curve points; decision contexts are
stripped in SQL and counts use the `(kind, at)` index without parsing payloads. A pre-existing layout bug was fixed:
visually hidden table labels widened every page at phone width (the Overview overflowed by 259 px).

Browser checks: the repository harness (`tests/browser/smoke.mjs`) now covers nine views and the Paper trading
tab; empty and populated fixtures pass every check in Chromium 141 (evidence and labelled fixture screenshots in
`docs/evidence/m7/`). A separate seeded book from the real engine and guardian rendered without console, page or
CSP errors at 1920×1080 and 390×844, with zero horizontal overflow on all nine views. Two test races were fixed
rather than retried: the harness checked search focus before the app's zero-delay focus task ran (failed 1 of 3
empty runs; now waits up to 2 s), and the kill -9 fault test let the engine open an unbounded number of positions
while the guardian was still starting (failed once in a loaded full run; the engine now starts once the guardian
supervises). These are short cloud checks, not the laptop's M7 acceptance.

Checks: **721 passed, 5 opt-in live tests deselected**; Ruff and strict mypy (115 source files) clean; strict
TypeScript build and Prettier; all seven contrast tokens pass on four surfaces. No live provider test, wallet,
signature or transaction was used for this work.

**M9 readiness report (later the same session).** `desk report readiness` writes `READINESS_REPORT.md` (default
`DESK_HOME/reports/`) with one verdict by a stated precedence over the PLAN §14.3 states, the forward period and
market-context gaps, observed/conservative/stress results, the $100 replica, each version's confidence matrix, data
integrity, unresolved risks, disclosures and a sources appendix naming the query behind every figure. The text is a
function of the data (its time is the latest recorded event; the bootstrap seed is fixed), so it regenerates
byte-identically; an audit row records each generation outside the text. While PAPER runs the collector refreshes
the confidence matrix and the report every 6 hours off the event loop, so H8's evidence-gated sizing cannot lapse
unnoticed and the dashboard shows the current verdict (Overview, Research). Tests cover precedence, reproducibility,
sources, gaps, the empty book, the CLI, the collector job, the dashboard field and that an ELIGIBLE verdict leaves
mode, approvals and LIVE gates unchanged (M9-05). The browser harness checks the verdict panel in both fixtures.

**Production-input audit (later the same session).** A separate research branch (`codex/council-research`,
documentation only, zero influence, no code changes) noted that H7's age-adjusted volume decay is unverified in
the ordinary builder path and that H1 lacks a verified migration time. Both were confirmed against the code, and
the audit was extended to every PAPER strategy, using the real feature builder instead of hand-made contexts:

- **Every production decision was NO_TRADE.** The builder always labels `volume_decay_adj` UNVERIFIED (token age
  is a provider claim); the H7 overlay required it OK, and an overlay's reasons veto every strategy. Fixed by
  evaluator **v4**: an overlay may act on an UNVERIFIED value (it can only remove eligibility); MISSING, STALE and
  CONFLICT still veto. v1–v4 all replay exactly.
- **H8 could never qualify.** It required developer holdings, which are never produced (no caller supplies a
  verified developer address). **H8 v3** requires the largest single holder ≤ 10% instead; it is a child of the
  published v2, so laptops that registered v2 register v3 cleanly, and conviction evidence is per exact version.
- **Remaining, documented gaps (not worked around):** H1 (verified migration time, verified organic share), H6
  (verified token age) and H2/H5 (event-chronology features not produced) record NO_TRADE. B1, B2, H3, H4 and H8
  can trade on real inputs. Trading on unverified claims for entries would contradict "unknown is never zero".

New tests build decisions from real builder output, prove v3's universal veto and v4's behaviour (acts on the
claim, still vetoes real exhaustion and missing inputs), and assert each strategy's input availability (M3-13).
The council research branch itself remains research only and was not merged.

## M8 completed: per-intent authorization, settlement and the LIVE executor (2026-10-19 UTC, Claude)

The gap Codex found in M8-06 is closed and the rest of the LIVE path is built. Tested only against fakes: no key
was used, no wallet funded, nothing signed or sent (M8-11 holds).

**Per-intent authorization (M8-06).** `desk live arm` builds the signer with `desk.live.authorize.LiveAuthorizer`.
Synchronously before every check that can lead to a signature, it opens fresh read-only snapshots and
re-establishes all seven gates for that exact intent: LIVE mode plus `DESK_LIVE_CONFIRM` in the signer's own
environment, a typed approval of the intent's strategy version, an ELIGIBLE readiness report at most 7 days old (or
a recorded override), the capital allocation within the ceiling, preflight (no STOP or LIVE halt, fresh LIVE
guardian heartbeat, fresh market data, the intent's quote recorded seconds ago, a fresh security PASS, the LIVE
position limit, one position per token) and a calibrated regime the strategy accepts. The approved strategy must
still choose the candidate when its decision is re-evaluated on current data. Unreadable evidence is a refusal.

**Settlement.** A landed swap reports what changed on-chain: the wallet's SOL and its own token-account changes,
and whether the token account was created (its rent). A transaction that lands but fails reports the fee it paid.
The balances read just before signing are journaled, so a signer restarted after a crash measures what landed and
reports it too. The signer can close the wallet's own empty token account (rent back) and answers read-only
lookups of any intent's journaled outcome.

**LIVE executor (`desk live run`, `desk live guardian`).** `desk live run` refuses unless gates 1 and 4 hold; it runs
discovery, PAPER and the LIVE engine together, and both books decide on the same security PASS concurrently. The
LIVE engine decides with typed-approved hypotheses only (baselines never trade LIVE), applies the §13.2 account
limits (2 positions, 10 trades a day, 6 requests a minute, 5% daily realized loss, 8% daily drawdown, 25% drawdown
from peak and 3 consecutive failed transactions — the last two persist until a typed `desk live resume`, which
restarts the peak and the count), sizes 10% of min(equity, allocation) within every cap, takes a fresh exact-size
quote and its sell-back against the strategy's round-trip cost limit, and checks the same authorization the signer
will apply, so refused intents are never sent. LIVE never compounds by itself: the signer caps each BUY at 15% of
the allocation, so the account grows only when the operator raises `live.max_capital_sol` (ceiling 2 SOL).

The independent LIVE guardian (heartbeat `guardian-live`, its own lock) reuses PAPER's marks, impairment and exit
rules. An exit sells the wallet's on-chain token balance (read from the chain, so an estimated booking never leaves
tokens behind), sells any remainder, then closes the token account; the position closes at exactly what the wallet
received. Refused, failed or expired exits are retried, become EXIT_BLOCKED after three and stay supervised;
tokens missing from the wallet halt LIVE entries (`RECONCILE_REQUIRED`). Every request is journaled in the LIVE
ledger before it is sent; a lost answer is resolved from the signer's journal before that position or any new
entry proceeds, and nothing is ever resent; a request that never arrived is closed only after it expires. Each
booking also resolves its request in one transaction, so a recovered outcome can never be booked twice.
`desk liquidate --all --live` now uses the guardian's exit path (or hands positions to the running LIVE guardian).
The dashboard's Live page shows the LIVE book read-only. New settings: `live.entry_slippage_bps = 100`,
`live.exit_slippage_bps = 300`.

**Discovery-to-PAPER audit.** Suspected: PAPER sizing needs a SOL/USD quote at most 120 s old, which every PAPER
test seeded itself. Verified not a bug: discovery records that quote seconds before it hands a PASS to PAPER. A new
test runs the real discovery engine into the real PAPER engine (real feature builder and decision, network mocked)
with the book's own quote an hour old; removing discovery's quote save makes it fail.

Checks: **795 passed, 5 opt-in live tests deselected**; Ruff and strict mypy (130 source files) clean; TypeScript
build and Prettier; Chromium smoke (empty and populated fixtures, all nine views) passed. Two halts that re-applied
themselves the moment `desk live resume` lifted them were found by the new tests and fixed (the peak and failure
count now restart at the resume).

## Evidence council: Codex's research finished as a record-only sidecar (2026-10-20 UTC, Claude)

Checked what the other AI left: the only unmerged work was Codex's `codex/council-research` (documentation and a
design verifier; no open pull requests or issues). It is now on this branch with its "build first" minimum version
implemented (details and remaining steps: `research/council/HANDOFF.md`, PLAN §14.6, ACCEPTANCE M6-15..18).

- Two research advisors record, for every recorded SHADOW/PAPER/LIVE decision, what they would say about a
  prospective long: C1_BRC (benchmark-residual continuation over 76 h of unbroken 15-minute history) and C2_ECDR
  (dislocation reversal; ABSTAIN when one is detected, because its pre-event quote panel is not collected).
  Influence NONE, zero added provider requests, research.db writes only, every input ended and received before the
  decision. `desk council report`; Research view on the dashboard. Expect mostly ABSTAIN for days: tokens must be
  first seen 96 h earlier and have 306 consecutive 15-minute bars.
- Resolved a research blocker: GeckoTerminal OHLCV bar time is the interval START (CoinGecko documentation plus a
  four-request probe; RESEARCH.md). New migrations: market 0009 (SOL/USD benchmark minutes from the existing market
  context payload, only once closed beyond the provider's 60 s cache), research 0004 (council records).
- No edge is claimed; no record can change an entry, exit, size or LIVE gate.

## Operator laptop update after a crash (2026-10-20 UTC)

The laptop crashed for a reason unrelated to the desk (operator's finding) during M2 run
`run_01M590ZRP2PPM8G96ZZRXB08XR`. A run that did not end cleanly can never pass `discovery-report
--require-hours 24`, and a restart does not join runs, so M2-14 stays **PENDING**; the captured data is kept. The
next attempt is a `desk paper --minutes 1440` run, which schedules the same discovery jobs and records the same
discovery statistics the acceptance check requires.

Laptop after updating to `d128b9f`: `desk db check` ok for market, research, runtime and ledger-paper (WAL).
`desk doctor`: 18 ok, 1 warning (swap 1045 of 2047 MB used), 1 failure: "Clock vs chain: getBlockTime: Block not
available for slot …". The failure was the doctor's own race (it asked for the block time of the newest confirmed
slot before the answering node had stored that block); it now steps back up to three slots and measures drift
from when the slot was read. New unit tests cover it; three live doctor runs against the public endpoint read
+0.9 to +1.6 s. Python 3.12.3, 266 GB free, NTP synchronized, keyed Jupiter and RPC configured (values hidden).

## The Robinhood desk and a clearer Scanner (2026-10-20 UTC, Claude)

Operator request: extend to all crypto tradable on Robinhood with a second, Robinhood-styled dashboard, "same or
better parameters", and make the memecoin desk just as good; and the question whether more than liquidity,
volume and price decide DEFER/REJECT/APPROVE (yes: on-chain safety, exact-ticket round-trip cost, holder
concentration, ~40 measured features, regime, money rules; the Scanner now shows them).

Decision on "Robinhood does the agentic trading": not adopted. Robinhood's agent account lets an AI model choose
trades, which PLAN §15 rules out; KESTREL decides with frozen rules and uses Robinhood's official API for prices.

Built (docs/ROBINHOOD.md): Ed25519-signed read-only Robinhood client (reproduces the docs' example signature),
local `desk rh keygen`, Coinbase public history, separate databases with append-only triggers, frozen strategies R1
trend breakout, R2 weekly momentum, R3 uptrend pullback and baselines hold-BTC and random coin, a history
simulator running the same code, the PAPER engine (both routes estimated for the exact size, cheaper buy, worse of
two estimates booked, 2% round-trip ceiling, hard stops on the best bid, $1,000 loss halt), `desk rh` commands and a
read-only Robinhood-style dashboard (light/dark, phone width) on port 8766. The memecoin Scanner gained a Safety &
cost column (market migration 0010 indexes the lookup).

First history simulation (2% round trip assumed, 20 starter coins, frozen before running): per dollar-year held,
3 years: hold BTC +57%, random −171%, R1 +83%, R2 −6%, R3 −231%; last year (BTC −26%): hold BTC −26%, R1 +36%,
others negative. Not changed after seeing it. Forward PAPER on Robinhood's prices is the evidence that counts and
needs the operator's read-only key.

Checks: Robinhood unit and integration tests (client 9, strategies 9, PAPER engine 7), dashboard data test for the
Scanner fields, Robinhood browser smoke (populated, light and dark, phone width) and the memecoin smoke (empty and
populated) pass; a one-minute keyless `desk rh paper` against live Coinbase history recorded a real session.

## Runs like an app; the 24-hour test in pieces (2026-10-20 UTC, Claude)

Operator request: split the 24-hour run into 4-hour pieces that add up in the app, resume on its own when the
laptop is on, and stop needing a terminal. Operator decision recorded: M2-14 now counts ≥ 24 h of cleanly
finished discovery runs within 14 days (active monotonic time, sleep excluded), with at least one 4-hour piece;
a crash loses only its piece. `desk discovery-report --require-hours 24` uses this rule (`--continuous` keeps the
old one). `desk up` now installs AND enables the services (both dashboards, both PAPER engines, guardian, backups)
so they start at every login; the Solana engine runs 4-hour pieces with Restart=always. Menu icons KESTREL and
KESTREL Crypto run `desk open`, which starts the dashboard if needed and opens it in an app window. The dashboard
shows test progress (Overview until passed, System always). The interrupted run of 2026-10-19 shows as lost; it
does not block the new total. Tests: segment accounting, launchers, services and CLI; both browser smokes pass.

## Robinhood crossed-quote continuation (2026-10-20 UTC, Codex)

Continued from the latest pushed Claude build, `71e2140`, in an isolated checkout on
`codex/robinhood-quote-validation`. Earlier parallel Solana LIVE work was preserved separately and was not merged
into this Robinhood fix. The operator supplied a laptop `desk rh doctor` report: databases OK, keys found, one
account with fee ratio 0.0095, 90 tradable USD pairs of 92, Coinbase history listing 403 USD products, and a
`best_bid_ask` failure for a crossed BTC quote. These are operator-reported observations, not cloud API results.

The quoted "fixing it now" change had not landed: `rh/client.py` still rejected every bid above its ask. The
official vendored v2 `Get Best Price` description says the feed aggregates partner exchanges and excludes size
and fees; it imposes no bid/ask ordering invariant. Parsing now preserves both finite positive sides, including
locked/crossed observations. Zero, negative, missing, boolean, nonnumeric and nonfinite prices remain invalid.
The doctor labels crossing, the dashboard API exposes `quote_crossed`, and the coin page labels the signed spread
and explains the separate exact-size estimates. No price sides are swapped or clamped.

Also fixed a related endpoint mismatch: `Pair.tradable` now requires `is_api_tradable=true`, as the v2 best-price
endpoint requires. Retail-only pairs therefore cannot invalidate an entire quote batch. PAPER still uses actual
exact-quantity estimated totals and its existing round-trip ceiling; a crossed display quote cannot bypass it.

Validation: **35 Robinhood unit/integration tests passed**; Ruff over `src tests` and strict mypy over **157 source
files** passed; TypeScript/Vite built the included assets. Chromium **154.0.8037.57** checked an offline crossed-quote
fixture in light/dark modes and at 375px, preserving the original prices and negative signed spread, showing the
new label/explanation, with no horizontal overflow or JavaScript errors. These fixtures do not supply forward or
profitability evidence. No private laptop credentials were read and no real Robinhood API request/order was sent.

Next on the laptop: fast-forward the fix, run `uv run desk rh doctor`, and restart already-running Robinhood workers
so they load the new code. With installed user services this is
`systemctl --user restart desk-rh-engine.service desk-rh-dashboard.service`; otherwise stop/restart those terminals.
Do not restart the Solana collector for this fix. Robinhood remains PAPER-only. Keyed forward PAPER results and
real-laptop acceptance are still pending; no additional 24-hour wait is needed before opening its dashboard.

## Continuous Robinhood multi-timeframe PAPER checks (2026-10-20 UTC, Codex)

Operator requested continuous data/analysis and multiple candle intervals rather than a daily entry appointment:
data frequency does not require trade frequency. Branch `codex/robinhood-multitimeframe` descends from the quote
fix `ec55bc0` and Claude `71e2140`; no unrelated Solana LIVE prototype was incorporated.

New version-2 R1/R2/R3 PAPER rules combine daily BTC regime, locally aggregated complete 4h trends, hourly
setups and 15m confirmation. Data downloads rotate bounded batches on the existing free quota while quote/stop
and analysis tasks run independently. Closed/available-at-decision data only; exact warm-ups and missing buckets
abstain. There is no 00:05 UTC gate on v2 entries and no historical catch-up fills after sleep. The original
daily backtest, two baselines and legacy position exit rules remain version 1; new performance is counted by
spec hash, without attaching the old v1 backtest to v2.

Added immutable per-asset/candle/spec claims (migration rh_market/0002) and an hourly-setup decision index
(0003). Repeated polls/restarts cannot replay a claimed candle or reopen a closed position on its same hourly
setup. Claims precede execution awaits; a crash can miss a PAPER opportunity rather than duplicate it.
Replay evidence includes bounded source-candle hashes, features, frame state, comparison-universe hashes,
missing assets and conditions. The source candle query now honors first receipt via `as_of`.

PAPER entries additionally recheck STOP/halt, fresh second estimate, current candle boundary and the relative
ticket cap after awaiting prices, and recompute the round trip at the worse estimated entry cost. Quotes in
the future are stale. Exit attempts are serialized per position. No real Robinhood order method, credential
change, LIVE activation, collector restart or wallet action was performed.

Dashboard coin charts now offer 15m/1h/4h/1d intervals and frame readiness; the System/report schedule shows
continuous checks and the next 15m candle close plus publication grace. Old v1 holdings/results are explicit.
See [ROBINHOOD_MULTIFRAME.md](docs/ROBINHOOD_MULTIFRAME.md) for exact rules, resource estimates, migration
behavior, failure policy, laptop commands and outstanding validation.

Validation: the broader offline suite passed **873 tests, 5 deselected** with one existing Starlette/httpx
deprecation warning. Five further timing/strategy/legacy/bootstrap cases were then added; the final affected
Robinhood suite passed **49 tests**. Ruff over src/tests, strict mypy over **158 source files**, TypeScript and Vite passed.
Final Chromium checks passed for populated AND empty fixtures in light/dark and at 375px; the populated check
exercised all four chart intervals, readiness, System timing and no phone overflow. Synthetic 90-asset feature
loading/conditions took 1.142s with 48.6 MiB peak process RSS in the cloud, not measured on the Dell. At that
fixture's 11,185-byte mean audit JSON, 90 × 96 checks imply about 92 MiB/day before SQLite/quote overhead.

Still outstanding: keyed laptop forward operation, actual sleep/network behavior, sustained resource/provider
budgets, Robinhood-specific backups/retention, and evidence that v2 improves after costs. The existing automatic
backup kinds do not include the two Robinhood databases. No historical or synthetic result validates v2 or
permits real-money promotion. Source data updates automatically as it is received; no import or 24-hour wait
is required to use this feature.

Final scheduling review also added a daily-maintenance barrier: every stale/missing daily asset gets a history
fetch attempt before legacy exits and baseline selection run, so rotating initial downloads cannot silently
shrink the baseline universe to the first batch. Errors remain explicit ineligible inputs; v2 checks do not
wait for that daily maintenance. The two-batch fixture verifies this without network calls.

## Codex's Robinhood work merged; the Robinhood desk learns (2026-10-20 UTC, Claude)

Checked first: Codex had pushed `codex/robinhood-quote-validation` (crossed quotes kept, API-unsupported pairs
filtered; the fix for the operator's `desk rh doctor` failure) and `codex/robinhood-multitimeframe` (continuous
15m/1h/4h/1d PAPER analysis, v2 rules). Both descend from `71e2140`; fast-forwarded after 878 tests, Ruff and
strict mypy passed (two UI files reformatted with Prettier; bundle rebuilt). My own duplicate quote fix was
discarded unused.

Operator request: the app should learn while it gathers data, aiming at profitable trades. Built the learning lab
(docs/ROBINHOOD_LEARNING.md): 255 rule variants in four families plus an L2 logistic model, simulated on up to 4
years of closed daily bars with measured or assumed round trips; monthly walk-forward selection judged only on the
following unseen month; three procedures (best, best three, model) must be profitable after costs and beat BTC on
the same days (stationary bootstrap, Holm) with >= 12 months and >= 60 trades before the best is promoted to PAPER
as `L1_LEARNED` (4 positions max, own version hash, exits by the rule that opened it, benched by its own forward
record). Runs monthly inside `desk rh paper`, on demand with `desk rh learn`; dashboard Learning page. Operator
decision recorded: `robinhood.learning = "paper"` (record/off available); LIVE and signer cannot import the lab.

First result (20 starter coins, 4 years, 30 unseen months, 2% round trip): best single −2.62%/trade, best three
+0.32%/trade (not significant), model −2.81%/trade; nothing promoted. v1 of the lab failed; v2 added low-turnover
families and a selection score able to choose them before their results were seen; both versions are hashed.
Tests: no look-ahead, honest stops and costs, ranking on known outcomes only, planted edge promoted, noise not,
engine entries/exits/benching/scheduling, paper-only import guard, Learning page in the browser smoke.

## 2026-10-20 — Earlier-opportunity workflow and $500 account amendment

Operator requested sustained research/development, prioritizing Robinhood, and superseded the $100
reference account with $500. Both defaults and current labels now follow configured capital; old positions,
cash journals and historical learning reports are retained. $100,000 learning cash and percentage caps remain
separate. An append-only event records existing Solana sizing changes at the original SOL/USD rate; RH records
future sizing reference changes. New learning dollar illustrations use the configured initial ticket and
carry its amount; past reports are not multiplied by five.

Delivered SHADOW-only opportunity sidecars for Robinhood and Solana, immutable receipt-time inputs and hashes,
all locally observed cohorts, honest missing outcomes, exact-original-quantity quote marks, CLI and an RH
Learning-page panel. No added provider calls or trading influence. Eight accessible primary documents were
partially read; speculative mechanisms and trade-policy experiments are clearly separated from findings.
[Research](research/opportunities/README.md), [validation](research/opportunities/VALIDATION.md), and
[handoff/operator commands](research/opportunities/HANDOFF.md) are the durable continuation. Prior $100 notes
above remain dated history. Long-duration acceptance and actual prospective net edge remain unproven.
