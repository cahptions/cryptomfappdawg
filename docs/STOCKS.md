# KESTREL Stocks — start here

The saved Claude branch completed the research/data modules, the desks (eleven then; the News and world desk made
twelve on 2026-11-03), validation and PAPER engines (M1–M6, through `972565b`). The continuation connects those modules to operator commands, a local dashboard
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
split-adjusted line/candlestick charts and volume, financial statistics, the committee and twelve desks,
bull/bear evidence and a dated calls/puts table. Missing observations remain missing; a chart breaks at
missing sessions rather than manufacturing candles. Top10 means up to ten qualifying names, never padding.
These are daily-close research views, not a live intraday ranking service. The one intraday view is Home's **Movers
today** card: during the session, the biggest moves (three up, three down) among the stocks the app is already quoting
on Schwab (the underlying's price with every Plays tracking quote, every five minutes, and with every option chain
collected today), each against its last stored close and stamped with its quote time. It costs no extra request and
covers only those stocks (it says how many), not the whole market.

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

## The `kestrel` command

`uv run desk open --desk stocks --install` also installs a `kestrel` command in `~/.local/bin`. Run it from any
folder, over SSH too:

- **`kestrel`** prints a plain-English health report and says which items need attention. It covers the app, the
  collector, whether it stays on and whether closing the lid suspends the computer, the last close analysed, stored
  prices, the Schwab sign-in, one live test quote, any failing job and the watchdog (see Always on).
- **`kestrel update`** pulls the latest version, installs it and restarts the app in the background. Saved data and
  PAPER preferences are kept.
- **`kestrel open`** opens the app window.
- **`kestrel signin`** does the weekly Schwab sign-in from the terminal, over SSH too, while the app keeps running.
  Open the printed link in any browser, log in, then paste the final address (it fails to load; that is expected)
  at the hidden prompt and press Enter within about 30 seconds.

`kestrel` also prints a **Version** line: the installed commit and its time, whether the running app is on it
(otherwise `kestrel restart`), and whether GitHub has a newer one (then `kestrel update`). The System page shows the
version too. **`kestrel options-history`** makes two read-only requests to check whether the Massive key reads
historical option prices (Massive's free Options Basic plan lists 2 years of daily option bars at 5 requests a minute);
it says what to add if the key covers stocks only. Installing the launchers also refreshes the desktop's icon cache and
menu database, so a recoloured icon shows without logging out (a panel pinned before may still need restarting).

`kestrel update` also refreshes the `kestrel` command itself, so new subcommands arrive with updates.
`kestrel restart` restarts the app in the background. **`kestrel learning`** prints, read-only, what the Learning
research account is doing: every book's P/L on the money put in, the research account against the $500,000 reference
on the very same orders (split into trades both made, trades the reference could only partly afford, and trades it
could not afford at all), the research account's results by recipe (structure, size, holding time), how much of its
profit came from its best five trades, and whether its per-day average is distinguishable from luck.

### Always on

On the computer that holds the Schwab sign-in (now the Dell), Stocks runs as a user service,
`desk-stocks-dashboard.service`: systemd restarts it 10 seconds after it stops for any reason (a crash, a failed
restart, a stop) and, with lingering on, starts it when the computer powers on, before anyone logs in. `kestrel
update` and `kestrel restart` install and enable it; `kestrel` shows an **Always on** line. Turning on lingering
needs an administrator once:

```bash
sudo loginctl enable-linger neo
```

While it runs, the service holds a sleep lock (`systemd-inhibit --what=idle:sleep`, as the crypto engines do), so
the computer does not go to sleep when it is left idle.

**Closing the lid.** A companion user service, `desk-stocks-lid.service`, starts and stops with Stocks and holds a
`handle-lid-switch:sleep` block lock as you (`systemd-inhibit --what=handle-lid-switch:sleep`), so closing the
laptop's lid does not suspend it while Stocks runs. KESTREL never changes a system-wide setting itself. Limits, said
plainly: logind lets a user take the lid lock only from an active desktop session, so at power-on, before you log
in, it is refused and retried every minute (closing the lid then would suspend); and a desktop's own power settings
may still act on the lid (on Linux Mint: System Settings → Power Management → "When the lid is closed" → "Do
nothing"). `kestrel` prints a **Lid** line: "Lid close won't sleep" when the lock is held (or the system ignores
the lid), otherwise why not. To make the lid never suspend this computer at all, whoever is signed in, run once
(it writes one logind drop-in file, then reboots):

```bash
sudo mkdir -p /etc/systemd/logind.conf.d && printf '[Login]\nHandleLidSwitch=ignore\nHandleLidSwitchExternalPower=ignore\nHandleLidSwitchDocked=ignore\n' | sudo tee /etc/systemd/logind.conf.d/kestrel-lid.conf && sudo reboot
```

Undo with `sudo rm /etc/systemd/logind.conf.d/kestrel-lid.conf && sudo reboot`. `systemd-inhibit --list` shows the
locks.

**Watchdog.** Every five minutes the always-on app, and the worker at the end of each pass, checks three things:
the daily research for the last closed session not finished 3 hours after the close (Settings → Phone alerts
changes the minutes), a collection job failing for more than 6 hours (counted from when the check first saw it
fail; the hours are a setting too), and the worker stopped for 15 minutes while Start PAPER is on, or stuck on one
step for 90 minutes. What it finds is kept in `~/desk-data/stocks-watchdog.json`, shown in Evidence → Data health
(Check data health) and on the **Watchdog** lines of `kestrel`, and, with phone alerts on, sent to your phone.
Error texts are never copied: a failing job is described by a category (authorization, rate limit, network...).

Until 2026-11-04 the app ran as a plain background process, so anything that stopped it left it off (and the
research for that day's close unrun) until something opened it again. Its log is in the journal:
`journalctl --user -u desk-stocks-dashboard.service -n 50`.

### Using another computer's KESTREL Stocks

A new Schwab sign-in with the same app ends the previous one within hours, and Schwab support has told individual
developers they can create only one app (Schwab's own documentation does not say either way; [SCHWAB.md](SCHWAB.md)).
So one computer (here the HP) holds the Schwab sign-in and collects; the other shows it. Run once on the other
computer:

```bash
cd ~/kestrel && uv run desk open --desk stocks --install --remote neo@192.168.0.10
```

It creates an SSH key if needed and adds it to that computer (type its password once), stops this computer's own
Stocks app (its data is kept), and from then on:

- the **KESTREL Stocks** menu launcher opens that computer's app in its own window, through an SSH tunnel on local
  port 18767 that is started once and reused (if the app is not running there, it is started there);
- `kestrel`, `kestrel signin`, `kestrel learning`, `kestrel restart` and any newer subcommand run on that computer;
  `kestrel open` opens the window here; `kestrel update` updates that computer and then this one's copy and command.

`--remote off` brings back this computer's own Stocks app.

### Moving Stocks to another computer

`kestrel move-from USER@ADDRESS` (or `uv run desk stocks move-from USER@ADDRESS`) brings another computer's Stocks
data — price history, Plays history, tracked plays, the PAPER books and Learning — to the computer you run it on,
which keeps Stocks from then on. For the move from the HP to the Dell, on the Dell:

```bash
ssh-copy-id neo@192.168.0.10                         # once, if `ssh neo@192.168.0.10 true` still asks for a password
kestrel move-from neo@192.168.0.10 --dry-run         # checks SSH and lists the HP's Stocks files; changes nothing
kestrel move-from neo@192.168.0.10 --stop-other      # stops Stocks on the HP first, so the copy is its final state
kestrel signin                                       # the Schwab sign-in is never copied
```

What it does, in order: refuses if the address is this computer; checks SSH works without a password prompt; with
`--stop-other`, stops and switches off the other computer's Stocks service; asks the other computer to make
consistent copies of its four Stocks databases with SQLite's online backup API (the same code as `desk backups
create`; a live database is never copied without its write-ahead log), which stay there in
`~/desk-data/backups/manual`; copies them here over SSH (scp) into a staging folder and checks every checksum,
SQLite's integrity and foreign-key checks, and that the ledger is PAPER. Only then does it stop Stocks here, move
this computer's own Stocks databases (with their `-wal`/`-shm` files) and derived reports into
`~/desk-data/backups/before-move/<time>` (never deleted), put the copies in place, run the migrations, start Stocks
again, and print what moved: price sessions, Plays sessions, tracked plays, PAPER trades and Learning positions,
beside what this computer had. If this computer was set up to show the other one's Stocks (`--remote`), it now runs
its own and the `kestrel` command is rewritten for it.

A failed SSH, a failed copy or a copy that fails a check stops the move before anything here changes. It needs free
disk for the copies plus 512 MB (the market database is several GB; the copy takes some minutes on a home network).
Not copied: `secrets.env` (copy the key lines you need by hand, `chmod 600`), the Schwab sign-in (sign in again
here with `kestrel signin`; a new sign-in ends the other computer's within hours) and Settings preferences.
Without `--stop-other` the other computer keeps running its own Stocks; stop it there so the two do not both collect.

### On your phone

**`kestrel phone`** (on the HP, or from the Dell, which runs it on the HP) puts the Stocks app on your phone, privately,
through [Tailscale](https://tailscale.com/kb/1312/serve), free for personal use. The app keeps listening on the HP only;
Tailscale Serve on the HP answers at the HP's private address (`https://appstuff.<your-tailnet>.ts.net/`) with a real
HTTPS certificate, only for devices signed in to your own Tailscale account, and hands each request to the app.
Nothing is put on the public internet (that would be Funnel, which KESTREL never uses). The app accepts exactly one
extra address, the HP's own Tailscale name; any other name is still refused.

Once, on the HP:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --operator=$USER      # prints a sign-in link
kestrel phone                           # prints the phone address
```

The first time, Tailscale may print a link to allow HTTPS certificates for your account: open it, allow, and run
`kestrel phone` again. On the phone: install the Tailscale app, sign in with the same account, open the printed
address and add it to the home screen. The HP must be on and awake. `kestrel phone off` stops it.
`kestrel phone arbitrage` does the same for Arbitrage on the computer that runs it, at port 8443.

### Phone alerts

Short plain messages to your phone, **off until you switch them on** in Settings → Phone alerts. Two free channels;
set either or both in `~/.config/desk/secrets.env` (never on the page, in the repository or in chat):

```bash
# ntfy: install the ntfy app on the phone and subscribe to the same topic. Pick a long random topic name: anyone
# who knows it can read it. Generate one with: echo kestrel-$(openssl rand -hex 12)
NTFY_TOPIC_URL=https://ntfy.sh/kestrel-<long random topic>
NTFY_TOKEN=tk_...              # only for a protected topic or your own ntfy server
# Telegram: the same bot and chat as the optional Telegram control (RUNBOOK.md → Optional Telegram)
TELEGRAM_BOT_TOKEN=123456:...
TELEGRAM_CHAT_ID=123456789
```

Then `kestrel restart`, Settings → Phone alerts → **Send test alert** (or `uv run desk stocks alerts test`), tick
**Send phone alerts** and save. `uv run desk stocks alerts status` prints the switches and the recent alerts.

Each of these can be switched off on its own:

- the Schwab sign-in ends within 24 hours (it lasts 7 days), or has ended;
- one of **My tracked plays** reached its take-profit or its stop (its return in percent; no dollar amounts);
- a PAPER lotto is worth 3× its cost (Lotto picks, Lotto picks · let them run, Random lottos);
- a stock on your watchlist filed an 8-K red flag (bankruptcy, accelerated debt, impairment, delisting notice, auditor
  change, non-reliance) or a late-filing notice (NT 10-K/10-Q), first seen after alerts were switched on;
- the Stocks worker stopped (for 15 minutes while Start PAPER is on) or stuck, or a job failing for longer than the
  hours you set (6 by default);
- the daily research not finished the minutes you set after the close (180 by default).

The always-on app checks every five minutes. Each event is sent once (an append-only alert log in
`stocks-research.db`, table `stk_alerts`); a failed delivery is retried every 30 minutes, four times at most; no more
than 6 alerts an hour and 24 a day go out, the rest wait for the next check. Messages never include a key, token, the
topic address or an error text from a provider. An alert never places an order: research and PAPER only.

## 3. Automatic collection and PAPER trading

Opening Stocks starts `desk stocks paper --continuous --price-days 20` in the background. There is no24-hour
cutoff for this app-owned worker. It checks again five minutes after each completed pass, collects up to20
missing price sessions, 20 companies and 30 rotating option underlyings per pass, and respects the free provider
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

The ranked option focus is capped at 150 names. On trading days from 9:30 to 16:30 New York time the worker
collects option chains every 15 minutes: SPY and QQQ every time plus a rotating batch from the watchlist and the
ranked focus (30 stocks a pass in all), and every contract a PAPER position holds. With Schwab each stock costs two
requests: its expiration list, then **one chain request covering every expiry it needs** (the nearest weekly, about
30, 60, 90 and 180 days, the one nearest a year out and the first after results; names with daily expiries such as
SPY need two to five, so no single answer runs to thousands of unused contracts), with up to 50 strikes either side of
the money and the stock's price in the same answer. A pass is about 70 requests, under a minute of Schwab's 120 a
minute, and the 150-name focus is covered about every 75 minutes, five or so times a session. Tradier still needs one
request an expiry. Outside those hours it collects once if that session's chains were missed. It does **not** provide
a daily full-universe option archive or continuous intraday history per name. Held contracts are included regardless
of the current rotating focus. Chains are kept 45 days (roughly 5 MB a session at this coverage, about 0.25 GB in all).

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
- Backups: `kestrel update` also installs and starts the hourly backup timer (`desk-backup.timer`) on the computer
  that runs Stocks. It copies the two PAPER books (`stocks-ledger-paper.db`, `stocks-learning-paper.db`) every
  hour (24 kept) and `stocks-market.db` and `stocks-research.db` once a day (3 kept, because the market file grows
  by gigabytes) into `~/desk-data/backups/stocks-hourly` and `stocks-daily`. Copies use SQLite's online backup, so
  Stocks keeps running; each is checked and checksummed, and files are readable by you only. A copy that would
  leave less than twice its size plus 512 MB free is skipped and the timer shows failed with the reason
  (`systemctl --user status desk-backup`). Check a copy with `uv run desk backups verify <manifest.json>`.
  `desk backups restore` does not take Stocks databases yet: to restore one, stop Stocks
  (`systemctl --user stop desk-stocks-dashboard.service`), verify the copy, move the current file and its `-wal`
  and `-shm` files aside, copy the backup's file in, then start Stocks again.
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
