# Continuing Claude's work


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
