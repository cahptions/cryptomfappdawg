# Continuing Claude's work

> **Latest — reliability audit and Stocks sizing:** read `research/reliability/HANDOFF.md` before older notes.
> It contains the actual fault matrix,1121passing offline tests, browser/resource results, update commands and
> external limits. Stocks event PAPER sizing now has versioned larger desired tickets with account/stop-risk/
> liquidity caps; crypto/options fees and capital are separate. This does not create intraday signals or validate
> profitability. New `desk stocks diagnose` is bounded/read-only and redacts values. No production data or worker
> was accessed. Preserve automatic Stocks PAPER and disabled real orders; see current update/restart instructions.

> **Current automation policy — supersedes earlier manual research/PAPER instructions:** operator explicitly
> requested automatic PAPER operation. Stocks dashboard lifespan now starts one combined collection/PAPER worker
> (`stocks paper --continuous --price-days20`). It runs while the dashboard server runs, without a24-hour cutoff.
> Missing inputs defer affected strategies; optional source failures do not block usable stored-data PAPER.
> Explicit Pause persists in private `stocks-automation.json`; shutdown preserves the auto-start preference.
> GET requests never spawn workers. Existing external workers are observed, not duplicated/signalled; when their
> lock releases, the lifespan monitor starts PAPER. Crashes/disk/preference errors surface without restart loops.
> PAPER/collect cancellation closes network resources before releasing locks; synchronous ledger transactions
> finish atomically. First PAPER processing waits for minimum history, so warmup does not consume the session.
> Mutable research previews continue after the day's PAPER step. Simulation timing/costs/capital/STOP and disabled
> brokerage orders are preserved. UI uses Pause PAPER / Start PAPER and no longer asks for a separate terminal.
> Validation:124 offline Stocks tests, Ruff, mypy38files; populated/empty Chromium navigation and PAPER/Settings
> flows plus readiness cases at1366px and375px passed. Production autostart was tested with mocked child processes;
> no operator process or provider was accessed. Laptop activation needs a one-time update and dashboard restart.

> **Stocks desktop launcher:** the common icon installer omitted Stocks even though `desk open --desk stocks`
> already existed. Installer now includes all three by default, supports selected-only installation, and respects
> the Stocks port. Operator command `uv run desk open --desk stocks --install` installs only its lime menu icon
> and opens the existing/background dashboard. It does not start collection, engines or services. Stocks has its
> own browser profile/class and full-workarea initial placement, with saved-placement behavior retained.
> Validation:53 launcher/CLI/service tests, Ruff/mypy and desktop-file-validate for all three entries passed.
> Actual Linux Mint menu installation/window behavior remains an operator check after pulling this update.

> **Latest collector failure:** operator pasted a traceback showing `fundamentals.scores` divided cost of
> revenue by zero prior-year revenue while building the research preview. Both gross-margin paths now guard
> nonpositive revenue. Undefined Piotroski/Beneish components keep the composite unavailable, including zero
> current-liability, asset-quality, depreciation, SGA and leverage denominators; no neutral ratio is substituted.
> Other computable metrics remain available. Reproduced both old gross-margin crashes;115 offline Stocks tests,
> Ruff and mypy passed. Research lists survive the synthetic zero-revenue company without any PAPER ledger writes.
> Ask the operator to pull `codex/finish-schwab-stocks`, then click Start research; dashboard can remain open.
> Existing data is reused. Do not claim laptop recovery until observed. The failure reached analysis, so the
> earlier20-session count is stale. Evidence: research/evidence/2026-10-21/operator_zero_revenue_crash.json.

> **Dashboard history status:** an operator screenshot showed benchmark/sector prices but unrecorded mover
> lists. Home previously called this "No qualifying names" and linked to "Connect data" even when configured.
> It now distinguishes unconfigured history, incomplete history, pending analysis, and recorded empty lists,
> showing downloaded-session counts and whether collection is running/stopped. Counts describe market sessions,
> not proof of eligibility for every stock. No warmup/filter, collector, PAPER or LIVE behavior changed.
> Five synthetic startup/list states passed in Chromium at1366px and375px. Actual laptop progress after the
> operator's20-session doctor report is unknown; do not claim this screenshot proves current collection health.

> **Operator quote check passed:** laptop `desk stocks doctor --online` returned Schwab connected, a quote
> received (2026-10-21 17:43:06.135 UTC), no configuration problems, and healthy WAL/integrity for all Stocks DBs.
> Orders remain disabled. Initial20-session history is not yet the per-stock60-observation research warmup.
> FINRA format handling was corrected: explicit JSON Accept, CSV support, partition/number validation and
> paging by response record count. FRED now requests/validates CSV. The operator's public VIX probe returned
> an HTML Site Maintenance page from the St. Louis Fed; this source outage needs no credential change.
> Retry at the next scheduled rates job; source recovery is not yet observed. Source/collection regression
> suite:31 passed, Ruff/mypy clean. Evidence: research/evidence/2026-10-21/operator_fred_maintenance.json.
> Do not describe a single successful quote as complete options coverage or forward trading acceptance.

> **Schwab onboarding follow-up:** keep `desk stocks dashboard` running while setting up credentials. Stop
> collection, not the dashboard. Settings now has an owned-collector pause control directly beside the Schwab
> form; a terminal collector must be stopped in its own collect/paper/refresh terminal. Saving is disabled until
> collection ends. Typed inputs survive pause/polling. Saved credentials deliberately are not filled back into
> fields; individual saved indicators report their presence. An exact stale-session rejection renews CSRF once
> and retries only the rejected request; other forbidden/network errors remain errors. Operator screenshot
> confirms local HTTPS callback registration accepted/Ready For Use. OAuth completion and quotes remain to verify.
> `collect` now cancels network/rate-limit waits on stop and waits for cleanup before releasing its lock; PAPER
> stop semantics are unchanged. Failed login attempts have an explicit Cancel button, with callback-thread
> termination checked before credential replacement. Identical Client ID/Secret values are rejected. Validation:
> 35 relevant offline tests plus Ruff/mypy and empty/populated desktop/phone browser flows passed.

> **Free-source onboarding correction, 2026-10-21:** Settings now has an owner-only source form, signup links,
> explanations of required/optional inputs, and explicit Start/Pause research controls. No-signup public feeds are
> integrated; actual stock history still requires the operator's free Massive Stocks Basic key. SEC needs only
> their contact email; Tradier is optional if waiting for Schwab. No operator credentials were created or read.
> The dashboard starts only its own fixed-command `desk stocks collect` child on an explicit CSRF-checked POST;
> it never starts on GET or signals a terminal-owned collector. Pause/closing requests a safe end after the pass.
> Source changes preserve unrelated keys and Schwab tokens and take effect at the next collection pass.
> The research-only path can refresh the same session as backfill arrives, using mutable preview lists/latest
> scores with no PAPER ledger or frozen history writes. Price-receipt/job revisions trigger UI updates while
> a pass is running. Optional SEC mapping failure does not block Nasdaq listings. Tests use isolated fixtures;
> authenticated finance-source access and actual laptop collection remain operator checks, not claimed successes.

> **Latest continuation, 2026-10-21:** Stocks/Schwab work is on **`codex/finish-schwab-stocks`**, based on
> Claude's M6 commit `972565b`. Read [docs/STOCKS.md](docs/STOCKS.md) for operator commands, architecture,
> bounded data/resource behavior, tests and outstanding validation; [docs/SCHWAB.md](docs/SCHWAB.md) for
> the default free local HTTPS callback and optional Tailscale fallback. Saved evidence is
> `research/evidence/2026-10-21/schwab_callback_verification.json`. No real Schwab login or order was made.
> The latest fetched Claude branch remained `972565b`; all earlier opportunity/$500 work is preserved.
> Next operator step: update this branch, `uv sync --frozen`, then `uv run desk stocks dashboard --open`.
> Configure the approved app privately in Settings, verify with `uv run desk stocks doctor --online`, then
> run the separate bounded Stocks PAPER worker. Broker approval and prospective evidence remain outstanding.
>
> **Home-page correction:** the operator wants a stock/options research hub, not an account-first landing page.
> Home now presents market benchmarks, qualified top10 mover tabs, research collections, options intelligence,
> breadth/sectors and a watchlist. Stock detail follows the screenshot's chart/metrics/chain/committee structure.
> Views use stored observations only, with bounded payloads and split/gap handling. Restart an already running
> Stocks dashboard after pulling the update. `desk open --desk stocks` is also supported.


> **New continuation, 2026-10-20:** use [research/opportunities/HANDOFF.md](research/opportunities/HANDOFF.md).
> The reference account is now $500 on both desks. Earlier $100 notes below are historical. The new
> opportunity lab is SHADOW-only; it neither promotes a strategy nor changes LIVE authorization.

Current Robinhood continuation: **`codex/robinhood-multitimeframe`**, including the quote fix `ec55bc0`,
based on Claude's **`71e2140`**. New R1/R2/R3 PAPER entries check closed 15m candles using 1h setups,
locally assembled 4h trends and the daily BTC regime; they no longer wait for the daily session.
Versioned v2 specs, immutable candle checkpoints, point-in-time data queries and hourly setup deduplication
separate new evidence from the archived daily v1 experiment. Existing v1 positions retain their exit rules.
See [the multi-timeframe handoff](docs/ROBINHOOD_MULTIFRAME.md), including laptop commands, rules,
data gaps, resource estimates and outstanding forward validation. Two additive Robinhood-market migrations
apply automatically; no credentials or existing candles/positions are rewritten. Robinhood remains PAPER only.

The underlying **`claude/funny-rubin-gq99vj`** build contains all of Codex's `codex/continue-claude` (through `32344da`)
plus the Paper trading tab, measured paper fees, the H8 moonshot hypothesis with evidence-gated conviction sizing,
hypotheses-before-baselines ranking, replay of every recorded evaluator version, the M9 readiness report, the fix
for always-NO_TRADE production decisions (`shadow-rules-v4`) and the completed M8 LIVE path. Details: the last
sections of [PROGRESS.md](PROGRESS.md).

Validation in the cloud: the offline suite, Ruff, strict mypy, the TypeScript build, contrast checks and a headless
Chromium check of the new tab at 1920×1080 and 390×844 passed. Nothing here used a wallet, signature or transaction.

## Get this version on the laptop

The dashboard reads `~/desk-data` as collection happens; there is no import step. If the 24-hour discovery run is
still going, let it finish before updating the folder it runs from (a restart would end that acceptance run).

```bash
cd ~/kestrel                     # or ~/kestrel-dashboard, if that is the folder you use for the dashboard
git status --short               # should print nothing; if it lists files, stop and keep them
git fetch origin codex/robinhood-multitimeframe
git merge --ff-only FETCH_HEAD   # preserves local work; stop if Git reports conflicts or divergent history
uv sync --frozen
uv run desk db check
uv run desk doctor
uv run desk rh doctor
```

Running processes keep the code they started with. Stop anything still running in a terminal (`Ctrl+C`), then
turn everything on as background services that also start at every login:

```bash
uv run desk up        # both dashboards, both PAPER engines, guardian, backups, menu icons
```

Open the dashboards from the menu (**KESTREL**, **KESTREL Crypto**) or `uv run desk open`. After a later update,
run `uv run desk down && uv run desk up` so the services restart on the new code.

The first start applies one additive database migration (`fee_observations`). Existing paper positions, strategy
versions and evidence are unchanged: every existing strategy version hash is identical, so open positions keep
their exact exit rules. A SHADOW session recorded before the update still replays (`desk replay`) with the
evaluator version that recorded it. Paper fees start on the conservative model and switch to measured fees by
themselves once the collector has stored six fee reports (about 30 minutes of running). The private keys stay in
`~/.config/desk/secrets.env`; nothing here asks for them.

Built assets are included; Node is needed only when changing UI source, via `scripts/build_ui.sh`.

## What changed in this update

- **Paper trading tab.** Plain-English activity (every look, skip, buy and sell with its reason), filters, the
  signal-to-trade funnel, skip reasons, open positions with stop/trailing/time-limit levels, closed trades after
  fees with observed/stress results, the realized curve, the $100 replica, fees and the moonshot watch. Reads are
  bounded and decision contexts never reach the browser.
- **Fees.** Jupiter's swap fee is inside every quote (verified) and never charged twice. Each round trip pays three
  network transactions, including closing the token account; network fees come from real Jupiter fee reports
  (p90 × 1.5) once six exist in 24 hours, else from the conservative model.
- **H8 moonshot.** A rare, fully confirmed explosive run, built to exit before the dump. It sizes up (1.5×, never
  above 15%) only after its own closed paper trades show PASS net expectancy and the signal has extra confirmation.
- **Ranking v3.** A qualifying hypothesis outranks the baselines; before this, B2's score scale crowded out every
  hypothesis. NO_TRADE still wins ties and whenever nothing qualifies.
- **Layout fix.** Hidden table labels no longer widen pages on a phone-width window.
- **Readiness report (M9 tooling).** `uv run desk report readiness` writes `~/desk-data/reports/READINESS_REPORT.md`
  with one verdict from recorded data. While `desk paper` runs it is refreshed every 6 hours and the verdict shows
  on Overview and Research. This is the signal to watch before considering real money; ELIGIBLE only permits a
  review, and LIVE still needs every gate.
- **Decisions on real inputs.** Every production decision had been NO_TRADE (an overlay vetoed on a value the
  builder always marks unverified) and H8 could never qualify (it needed an input never produced). Fixed by
  evaluator `shadow-rules-v4` and H8 v3; older runs still replay with their own evaluator.
- **The Robinhood desk learns (PAPER only).** A monthly walk-forward lab (255 rule variants + a logistic model on up
  to 4 years of daily bars, measured costs) promotes a procedure to PAPER as `L1_LEARNED` only when it is profitable
  and beats BTC on months it never saw; benched if its own paper trades contradict history. Learning page;
  `desk rh learn`; `robinhood.learning = paper|record|off`. First run: nothing passed. Codex's crossed-quote fix
  and v2 multi-timeframe analysis are merged. docs/ROBINHOOD_LEARNING.md.
- **Runs like an app; the 24-hour test comes in pieces.** `desk up` installs and enables both dashboards, both
  PAPER engines, the guardian and backups (they return at every login) and adds the menu icons KESTREL and KESTREL
  Crypto (`desk open`). The Solana engine runs 4-hour pieces; every cleanly finished piece counts toward the 24 hours
  (one must reach 4 h; sleep excluded; 14-day window); progress is on Overview and System. `desk down` turns it off.
- **The Robinhood desk (separate, PAPER only).** `desk rh keygen|doctor|backtest|paper|report|dashboard`; its own
  databases (`robinhood-market.db`, `robinhood-ledger-paper.db`) and dashboard on port 8766. Needs a read-only
  Robinhood credential for prices; without it, history and signals are recorded and nothing trades. No order code
  exists. Design and first history results: `docs/ROBINHOOD.md`.
- **Memecoin Scanner shows the deciding factors.** A Safety & cost column (verdict, round-trip cost, top-10 holder
  share, reasons). Market migration 0010 adds an index for it.
- **Evidence council (research only).** Codex's two advisors now record what they would have said at every
  decision (`desk council report`, Research view). Zero influence, zero added requests; mostly ABSTAIN until
  tokens have 76 h of history. First start applies market migration 0009 and research migration 0004.
- **LIVE (M8) completed, never used.** The signer re-checks all seven gates for every BUY just before signing.
  `desk live run` (PAPER plus LIVE) and the LIVE guardian trade only through the signer and book every amount from
  the wallet's measured balance changes; lost answers are looked up, never resent. Nothing runs LIVE until you set
  the mode, capital and approval and arm the signer: see the runbook's "Going LIVE". No wallet was funded.

## What changed after Claude

- Added a React/TypeScript dashboard served by FastAPI on loopback. Overview, scanner, positions, strategies,
  research, audit, system and LIVE panels read the existing databases; no sample balances are substituted.
- Candidate dossiers show recorded checks and thresholds. Position details retain actual marks/events and
  their timestamps; stale or blocked-exit values remain labelled. Confidence reads the latest persisted report,
  rather than recalculating bootstrap samples every refresh.
- A shared snapshot cache, one SSE stream per browser tab, bounded visible history, abortable requests and
  cleanup avoid accumulating browser history or overlapping refresh requests. SSE sends once per second;
  database summaries are cached for two seconds. This does not prove absence of leaks over a day-long run.
- Added local controls for starting SHADOW/PAPER, stopping entries, generating confidence, and disarming.
  The UI cannot arm LIVE, approve a strategy, withdraw funds or resume entries. Resume is `desk resume` in
  the terminal. CSV/JSON downloads include the latest 10,000 positions and preserve exact raw quantities.
- Bound the server to `127.0.0.1`, reject foreign Host/Origin requests, require a session CSRF token for
  mutations, and apply a restrictive CSP. React renders provider text as inert text; no token images or remote
  fonts are loaded. CSV fields are protected against spreadsheet formula evaluation.
- Added `desk up [--paper]`, `desk down`, `desk services install/status`, verified online backup archives,
  rotation and guarded restore. Optional Telegram status/stop code stays off until explicitly configured and
  invoked. No Telegram message was sent during development.
- Added OS locks for entry/collection, dashboard, guardian and Telegram workers. A second supervised worker
  using the same role/data directory refuses startup. Guardian operation remains independent of the engine.
- PAPER now rechecks STOP, halts, guardian freshness and context after a delayed quote and before opening.
  Concurrent exit attempts settle cash only once. Blocked exits retry independently of mark updates.
- Corrected a material M8 gap: the seven-gate report was disconnected from signer BUY processing, so production
  BUY was made to refuse before ordering/signing. Claude has since connected the production per-intent
  authorizer (see "What changed in this update"); a signer without it still refuses every BUY.

## Operation

Token dossiers now include recorded 5-minute candlesticks, USD volume, EMA20/EMA50 and Wilder RSI14.
The collector backfills at most two pools per minute through the existing shared free GeckoTerminal quota,
prioritizing held positions, then a bounded recent candidate set. Charts are descriptive and do not change
entry approval. Missing history and indicator warm-up remain explicit; gaps restart indicator warm-up.
Closed bars and pool/base-mint identities are checked before storage. No data is fetched on chart GETs.
The chart addition passed 21 indicator/ingestion tests, 45 affected integration/API/storage tests, strict mypy
(122 source files), Ruff, TypeScript/build, and a real Chromium check of 80 candles and all indicator paths.

The learning book still has $100,000 simulated cash, with tickets based on $100 plus realized PAPER profit and
up to ten concurrent positions within the quote budget. Its $100-account replica remains separate, conservative,
and an estimate. Research book growth is not evidence to increase real position sizes; confidence-qualified
growth and relative account caps remain requirements for any future real sizing integration.

To stop new PAPER entries while leaving exits supervised:

```bash
cd ~/kestrel
uv run desk stop
uv run desk paper-report
```

Optional desktop services, only after a current terminal-run acceptance session finishes:

```bash
uv run desk up                       # installs/starts dashboard and backup timer
uv run desk up --paper               # also starts guardian and 72-hour PAPER engine
uv run desk services status
uv run desk down                     # leaves the exit guardian running
```

Engine and guardian services independently hold idle/sleep inhibitors. Their memory caps are 600 MiB and
200 MiB; the dashboard service cap is 200 MiB. These are service limits, not measured laptop consumption.
Leave the laptop plugged in and lid open until actual Cinnamon/lid behavior has been checked.

Backups use SQLite's online backup API, verify checksums/integrity/mode, and retain 24 hourly ledger archives
plus seven daily market/research archives. Backups on every position state change are still unconnected.
Restore requires all workers stopped, typed confirmation, verified role/mode, worker locks and a backup of the
existing destination. See [the runbook](docs/RUNBOOK.md) before recovery.

## Remaining acceptance

Automated development can proceed while elapsed-time runs continue. A short fixture test cannot close any of
these requirements:

1. Finish the actual M2 24-hour discovery run and require a passing duration/audit report.
2. Record the M3 six-hour SHADOW session and unchanged replay from actual captured inputs.
3. Complete 72 hours of PAPER with reconciliation, guardian/failure evidence and provider-budget checks.
4. Run the dashboard on the Dell Ryzen 7/16 GiB Mint laptop; check responsiveness, memory over time,
   accessibility, power/lid behavior and the beginner operations checklist.
5. LIVE was tested only against fakes. Before real money: the M9 verdict, your approval, `desk live validate` on
   the laptop, and optionally one tiny calibration trade with its own approval (PLAN §16.6).
6. Collect the required forward-validation calendar time and sufficient closed trades for M9. No profitability
   or LIVE readiness follows from implementation tests or simulated examples.

Provider credentials remain in the existing private secrets file. No paid APIs, remote machine, GPU model,
wallet funding, transaction signing or transaction submission is needed for the dashboard handoff.
