# KESTREL laptop runbook

KESTREL keeps runtime data in `~/desk-data` by default. Updating the Git repository does not replace that data.
Your existing provider keys stay in `~/.config/desk/secrets.env`; do not paste them into chat or put them in Git.
The dashboard is local to this laptop at `http://127.0.0.1:8765`.

## Update and check

Finish or stop an existing collector before starting a second one against the same data directory.
Use the branch and update commands supplied with your current handoff, then run:

```bash
cd ~/kestrel
uv sync --frozen
uv run desk doctor
uv run desk db check
```

The checked-in dashboard assets are ready to serve. Node and npm are not required to run the app.
Only when changing the frontend source, rebuild its locked dependencies and assets:

```bash
cd ~/kestrel/webui
npm ci
npm run build
```

No paid provider subscription, GPU, remote server or model API is required. The Dell's shared GPU memory is not
a trading resource. Keep Firefox's large conversation tabs and other heavy workloads in mind when checking RAM.
Cloud test measurements do not measure this laptop.

## Open the dashboard

```bash
cd ~/kestrel
uv run desk dashboard
```

Open `http://127.0.0.1:8765` in Firefox. Keep that terminal open. `Ctrl+C` stops the dashboard process; it does not
close PAPER positions. The mode badge must say PAPER or SHADOW for the corresponding data. The Live page shows the
seven gates and, once `desk live run` has created it, the LIVE book; it can disarm the signer but never arm it.

## The 24-hour discovery test, in pieces

The 24 hours no longer have to be one unbroken run (operator decision 2026-10-20). Every discovery or PAPER run
that finishes cleanly adds its active hours, including a run stopped by shutting the laptop down. Sleep does not
count, and a crash or power loss loses only the piece in progress. At least one piece must last the full 4 hours,
and only pieces from the last 14 days count. With `desk up` the engine runs in 4-hour pieces by itself; the
dashboard shows the total on Overview and each piece on System.

```bash
uv run desk discovery-report --require-hours 24              # PASSED once 24 h are counted
uv run desk discovery-report --require-hours 24 --continuous # the old rule: one unbroken run
```

A successful discovery test alone does not establish a profitable strategy or complete the 72-hour PAPER or
60-day forward-validation requirements.

## PAPER operation and stopping entries

```bash
cd ~/kestrel
uv run desk paper --minutes 4320
```

This starts simulated trading and an independent guardian. PAPER money is simulated. A NO TRADE result is expected
when safety checks, data freshness, confidence or costs fail. Larger positions require qualified realized profit,
adequate confidence and exact-size liquidity/cost checks; account growth alone does not require a larger trade.

To prevent new entries from any terminal:

```bash
cd ~/kestrel
uv run desk stop
uv run desk paper-report
```

The STOP file and ledger halt prevent new PAPER entries. The guardian stays running to supervise existing positions.
Do not kill the guardian while positions remain open. A blocked exit remains a visible position; it is not treated
as sold. `desk resume` requires an explicit local confirmation after the reason for stopping has been resolved.
An entry engine stopping or crashing must not stop its independent guardian.

The **Paper trading** tab in the dashboard (`g` then `t`) shows each look, skip, buy and sell with its reason, the
open positions' stops and time limits, closed trades after fees and the moonshot watch.

## Readiness report

```bash
cd ~/kestrel
uv run desk report readiness
```

This writes `~/desk-data/reports/READINESS_REPORT.md` and prints its single verdict. While `desk paper` runs, the
collector rewrites it every 6 hours (with a fresh confidence matrix) and the dashboard shows the verdict on Overview
and Research. The report reads recorded data only; generating it changes no mode, approval or LIVE gate. Keep the
file if you want a record: it is regenerated in place.

## Evidence council (research records)

```bash
uv run desk council report        # add --json for the full record
```

Two research advisors record what they would have said at each decision; they never change a trade. ABSTAIN means
the desk could not know (young token, short history, stale data), not a negative view. The filter diagnostic stays
BLOCKED until PAPER has closed trades, then INSUFFICIENT EVIDENCE until 300; neither is a reason to change anything.
`uv run desk council run` advises any backlog at once; the PAPER collector already does it every 15 minutes.

## The Robinhood desk

A separate desk with its own data and dashboard (details: `docs/ROBINHOOD.md`). It can run at the same time as the
Solana desk: different worker lock, databases and port.

```bash
uv run desk rh keygen      # once: prints the PUBLIC key for Robinhood (web classic → Account → Crypto → API trading)
uv run desk rh doctor      # after ROBINHOOD_API_KEY is in secrets.env
uv run desk rh paper       # Ctrl+C to stop; decisions happen once a day after 00:05 UTC
uv run desk rh dashboard   # http://127.0.0.1:8766
uv run desk rh backtest --fetch --days 1095   # history simulation (not evidence)
```

Create the Robinhood credential with read actions only. Without a key the desk still records what it would do
(SKIP NO_ROBINHOOD_PRICE) and the dashboard's System page shows the setup steps. `touch ~/desk-data/STOP` stops new
entries on both desks; exits continue.

## Run it like an app (autostart, no terminal)

User services require no `sudo`. One command turns everything on, now and every time you log in:

```bash
cd ~/kestrel
uv run desk up
```

That installs and enables the dashboards (Solana on 8765, Robinhood on 8766), the Solana PAPER engine (4-hour
pieces that restart by themselves), the Robinhood PAPER engine, the independent exit guardian and hourly backups,
and puts three icons in the menu: **KESTREL**, **KESTREL Crypto** and **KESTREL Stocks**. Clicking one opens its dashboard in its own
window (Chrome or Chromium "app" window when installed, otherwise a new Firefox window) and starts it if it is not
running. `uv run desk open --desk robinhood` does the same from a terminal. The first time, KESTREL opens on the
left half of the main screen and KESTREL Crypto on the right; move or resize either and it reopens where you left
it (each has its own small browser profile in `~/desk-data/windows`, and its own panel entry). `Super+←` and
`Super+→` snap a window to half the screen.

Stocks can be installed and opened independently with `uv run desk open --desk stocks --install`. Its lime icon
opens the research hub on8767 on demand; it initially uses the full work area. Dashboard startup automatically
starts its combined PAPER/collection worker unless paused. **Pause PAPER / Start PAPER** controls both. Stocks is not added to the older desks' autostart services by installing its icon.

`uv run desk down` stops the engines and dashboards and keeps them off at login; the guardian keeps supervising
open paper positions and backups continue. `uv run desk up --dashboard-only` starts dashboards and backups without
the engines. `uv run desk services status` shows every unit. Services start when you log in; to start them at boot
before anyone logs in, run `loginctl enable-linger` once.

Units are `desk-dashboard.service`, `desk-engine.service`, `desk-guardian.service`, `desk-rh-engine.service`,
`desk-rh-dashboard.service`, `desk-backup.service` and `desk-backup.timer`. The engine has a 600 MiB limit; guardian and dashboard each have 200 MiB limits. These limits
are enforced by systemd only when running those units. They are not promises about total desktop RAM usage.
Both engine and guardian services hold independent idle/sleep inhibitors while running. The guardian is supervised
independently; stopping the engine must leave
it alive while open positions remain. Check the real laptop's systemd and power-manager behavior before relying
on unattended operation.

```bash
systemctl --user status desk-engine.service desk-guardian.service desk-dashboard.service desk-backup.timer
journalctl --user -u desk-engine.service -u desk-guardian.service --since "30 minutes ago"
```

If a memory limit is hit repeatedly, stop entries and inspect logs and workload. Do not remove memory limits as a
first response. `~/desk-data/logs/desk.jsonl` is rotated and redacted; inspect it locally before sharing excerpts.
No secret value is embedded in generated units; the app reads your existing private secrets file.

## Backups and a safe restore

The opt-in hourly backup timer uses SQLite's online backup API, so committed WAL data is included. It snapshots
existing PAPER/LIVE ledgers hourly and market/research databases daily. Default retention is 24 hourly and 7 daily
archives. Backups on every position-state change remain to be connected; the hourly timer does not claim that
coverage. `runtime.db` is rebuildable and is excluded. Archive metadata records SHA-256, byte count, database role
and creation time; integrity and ledger mode are checked. Each database is individually consistent; the set is not
one transaction across databases. Backups do not include private keys or the raw-response directory, so preserve
those separately when migrating machines. `desk db backup` remains available for an immediate online copy.

```bash
cd ~/kestrel
uv run desk backups run
uv run desk backups create
```

The create command prints the new `manifest` path. Use `desk backups verify` followed by that path to check it.

Before restoring: stop new entries, finish supervised exits, stop every database worker including the dashboard,
collector/entry engine, Telegram worker and guardian,
then inspect the archive's manifest and verify it with the backup CLI. Restoring refuses active workers and a
PAPER/LIVE role mismatch. It backs up the current destination before replacement, validates the prepared copy,
then replaces only the selected database. The original archive stays untouched. Never restore by copying a main
SQLite file over an active database or deleting an active WAL file.

Keep those workers stopped until the restore finishes. Process checks cannot identify an unknown external SQLite
client; close any database browser or custom script too. Do not open another KESTREL session during recovery.

After restoration, run `desk db check`, `desk doctor`, and `desk paper-report` locally before restarting workers.
Keep STOP set until reconciliation and positions make sense. A successful checksum proves file consistency,
not that restoring historical account state is an appropriate operational choice.

## Optional Telegram

Telegram is off unless you explicitly configure and run its worker. It needs `TELEGRAM_BOT_TOKEN` and an exact
integer `TELEGRAM_CHAT_ID` in your private secrets file or process environment. Keep that file mode `600`.
Only `/status` and `/stop` are accepted, only from the configured chat. Other chats and commands are ignored.
There is no Telegram resume, arbitrary command execution, signing or LIVE arm command. `/stop` stops PAPER entries
and leaves the guardian supervising. If its reply says the halt could not be confirmed, run `desk stop` locally.
The local daily-report builder does not send anything by itself.

## Going LIVE (only after the readiness report and your own decision)

Nothing here happens by itself, and every step is yours. The readiness report must say ELIGIBLE FOR MANUAL LIVE
REVIEW for the version you approve; that verdict only permits this review.

1. **Wallet.** `uv run desk live keygen` once. It prints the public address only; the encrypted keystore is
   `~/.config/desk/hot-wallet.json`. Keep a backup of that file and remember the passphrase. Never paste either
   anywhere.
2. **Zero-cost check.** `uv run desk live validate` builds a real order for a public address, inspects and
   simulates it, and sends nothing.
3. **Capital, in a private copy of the configuration** (so a code update never overwrites it):

   ```bash
   mkdir -p ~/.config/desk && cp -r ~/kestrel/config ~/.config/desk/config
   ```

   In `~/.config/desk/config/default.toml` set `mode = "LIVE"` under `[desk]` and `max_capital_sol = "0.5"` (your
   amount; ceiling 2 SOL) under `[live]`. After each code update copy the folder again and re-apply those two lines;
   a setting the copy lacks makes the desk refuse to start rather than guess. Send the hot wallet the allocation
   plus the 0.05 SOL reserve plus about 0.01 SOL for fees. Tickets are 10% of min(equity, allocation): 0.5 SOL is the
   smallest allocation that clears the 0.05 SOL minimum ticket.
4. **Approve one version.** `uv run desk live approve <version>` with the typed confirmation.
5. **Arm.** Terminal 1:

   ```bash
   export DESK_CONFIG_DIR=~/.config/desk/config DESK_LIVE_CONFIRM=yes
   uv run desk live arm             # type the passphrase; leave this terminal open (closing it disarms)
   ```

   The signer checks gate 1 in its own environment, so both variables belong in this terminal too.
6. **Run.** Terminal 2, after stopping any `desk paper` run (LIVE runs PAPER in the same process):

   ```bash
   export DESK_CONFIG_DIR=~/.config/desk/config DESK_LIVE_CONFIRM=yes
   uv run desk live run             # refuses to start unless the mode and capital are set
   ```

   The LIVE guardian starts by itself and inherits these settings.
7. **Watch.** `uv run desk live status` (with the same `DESK_CONFIG_DIR`) and the dashboard's Live page. Every BUY is
   re-checked against all seven gates by the signer immediately before signing; a missing gate refuses it.

Stopping and recovering:

- `uv run desk stop` stops new PAPER and LIVE entries; open positions stay supervised and keep their exits.
- `uv run desk live disarm` locks the signer. Exits then cannot be signed either, so stop entries first and disarm
  when no LIVE position is open, unless you mean to freeze everything.
- `uv run desk liquidate --all --live` sells every LIVE position now and reports each result. A position that is not
  CLOSED stays open and supervised.
- A LIVE halt (`CUMULATIVE_DRAWDOWN`, `CONSECUTIVE_FAILED_TX`, `RECONCILE_REQUIRED`) stops LIVE entries until you
  have looked at why and typed `uv run desk live resume`. `RECONCILE_REQUIRED` means the wallet no longer holds a
  position's tokens: check the wallet on a block explorer before resuming.
- After a crash, re-arm and restart. Requests whose answers were lost are looked up in the signer's journal and
  booked once; nothing is resent.

LIVE amounts are booked from what the wallet's balances actually changed by. Results so far were tested only
against fakes; the first real trades are the first real measurement.

## Failure recovery

- **Providers fail or rate-limit:** entries should defer or halt; inspect provider health and allow cooldowns.
  Keep the guardian running for open positions. A missing quote is not evidence of a successful exit.
- **Laptop sleeps or loses power:** restore stable power/network, inspect suspend-gap and stale-data warnings,
  stop entries, reconcile and re-quote before allowing new entries.
- **An engine crashes:** the independently supervised guardian must remain alive. Inspect the cause before
  restarting entries. A stopped dashboard does not imply that trading workers stopped.
- **Disk or integrity problem:** stop entries, protect current evidence, verify an archive and follow the stopped-worker
  restore procedure. Never edit an applied SQL migration or a cash-journal row to silence a consistency failure.
- **A page consumes gigabytes:** Firefox `about:processes` identifies the site. A large ChatGPT page process is separate
  from KESTREL's Python workers. Reloading a browser page does not stop a collector running in a terminal.

The 24-hour discovery run, six-hour SHADOW replay evidence, 72-hour PAPER run, real-laptop lid/power checks,
manual dashboard accessibility/performance review and required forward-validation calendar time remain separate
acceptance evidence. Automated tests cannot substitute for elapsed time or an actual laptop measurement.
