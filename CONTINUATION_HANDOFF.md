# Continuing Claude's work

Branch: `codex/continue-claude`, based on Claude's `c29501a` from `claude/funny-rubin-gq99vj`.
The earlier parallel prototype is preserved on local branch `codex/paper-engine` at `d58bd83`; its incompatible
ledger/context migrations were not merged. This branch preserves Claude's database migration history and
later PAPER research decisions.

Validation: **672 offline tests passed**, 5 opt-in live checks deselected; Ruff, strict mypy (120 source files),
TypeScript/build, contrast checks and empty/populated browser smoke checks passed. Browser reports and fixture
screenshots are in [docs/evidence/m7/](docs/evidence/m7/). These checks ran in the cloud environment.

## Get this version on the laptop

Let any existing collection or PAPER acceptance run finish before switching its checkout. Do not run a second
collector against that data directory. These commands fetch code; they do not replace `~/desk-data` or your
private keys.

```bash
cd ~/kestrel
git status --short
git fetch origin
git switch codex/continue-claude
git pull --ff-only origin codex/continue-claude
uv sync --frozen
uv run desk db check
uv run desk doctor
uv run desk dashboard
```

If `git switch` reports local edits, keep that output and preserve those edits before switching; do not reset
or force checkout. Open **http://127.0.0.1:8765** in Firefox. The dashboard command stays running in its terminal.
Built assets are included; Node is needed only when changing UI source, via `scripts/build_ui.sh`.

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
- Corrected a material M8 gap: the seven-gate report was disconnected from signer BUY processing. Production
  BUY now refuses with `LIVE_ENTRY_AUTHORIZATION_UNAVAILABLE` before ordering/signing. The internal policy
  callback used by isolated tests is not exposed through configuration, HTTP or signer JSON. SELL recovery
  and restricted withdrawal paths remain available. M8 is incomplete until production authorization exists.

## Operation

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
5. Connect and validate trusted production LIVE entry authorization before considering M8 complete.
6. Collect the required forward-validation calendar time and sufficient closed trades for M9. No profitability
   or LIVE readiness follows from implementation tests or simulated examples.

Provider credentials remain in the existing private secrets file. No paid APIs, remote machine, GPU model,
wallet funding, transaction signing or transaction submission is needed for the dashboard handoff.
