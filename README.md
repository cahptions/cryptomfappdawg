# Solana Quant Desk (working brand: KESTREL)

A locally operated research and trading desk for Solana memecoins that discovers opportunities, rejects unsafe
tokens, paper trades under realistic execution costs, measures whether any strategy has a statistically defensible
net edge — and stays in PAPER mode indefinitely if it doesn't.

> **Status:** M0 approved; **M1 passed**. **M2 discovery and safety implemented; 24-hour acceptance pending** — see
> [PROGRESS.md](PROGRESS.md) for test results, including the successful laptop 60-minute run.
> M3 features, M5 PAPER, M6 statistics, and M7 dashboard/operations are implemented; timed and real-laptop
> acceptance stays separate. See [CONTINUATION_HANDOFF.md](CONTINUATION_HANDOFF.md).
> M8 has signer infrastructure, but production BUY authorization is incomplete and BUY requests are disabled.
> No AI/LLM and no paid services are used (operator decision).

## Documents
| Document | What it is |
|---|---|
| [PLAN.md](PLAN.md) | Architecture, strategy hypotheses, risk controls, costs, milestones, decisions |
| [RESEARCH.md](RESEARCH.md) | Verified facts about every data source, with evidence tags and source URLs |
| [ACCEPTANCE.md](ACCEPTANCE.md) | Objective tests each milestone must pass |
| [PROGRESS.md](PROGRESS.md) | Current status, measured results, and the exact next step |
| [CONTINUATION_HANDOFF.md](CONTINUATION_HANDOFF.md) | Latest Claude continuation, update commands and checks |
| [docs/RUNBOOK.md](docs/RUNBOOK.md) | Dashboard, services, backups and recovery on the laptop |
| [docs/ROBINHOOD.md](docs/ROBINHOOD.md) | The separate Robinhood desk: liquid crypto, PAPER on Robinhood's prices, its own dashboard |
| [config/](config/) | `default.toml` (all settings), `safety_ceilings.toml` (hard limits), `providers.toml` (data sources) |
| [docs/design/kestrel-concept.html](docs/design/kestrel-concept.html) | Static visual concept of the dashboard (sample data only) |

---

## Quick start on Linux Mint (your laptop)

You type these commands in the Terminal. Lines starting with `#` are explanations, not commands.

### 1. Install `uv` (one time)
`uv` installs the exact Python packages this project was tested with.
```bash
sudo apt update
sudo apt install -y pipx git
pipx install uv
pipx ensurepath
# close the terminal and open a new one so the `uv` command is found
uv --version
```

### 2. Get the code
```bash
git clone https://github.com/cahptions/cryptomfappdawg.git ~/kestrel
cd ~/kestrel
git checkout claude/funny-rubin-gq99vj
```
For the current handoff, follow [CONTINUATION_HANDOFF.md](CONTINUATION_HANDOFF.md). An existing collection process
does not automatically update when a branch is published; let the running test finish before switching its code.

### 3. Install
```bash
cd ~/kestrel
uv sync --frozen
uv run desk --version        # should print: desk 0.1.0
```
`--frozen` means "install exactly what is in `uv.lock`" (every package is checked against a hash).

### 4. Health check
```bash
uv run desk doctor
```
This checks your laptop (disk, memory, swap, clock, battery) and makes one or two **read-only** requests to each
data provider. It never shows your keys. **Paste the whole output into the chat** so it can be recorded.

### 5. The M1 acceptance run (60 minutes of read-only data collection)
Plug the laptop in, then:
```bash
uv run desk collect --minutes 60
```
Leave it running; at the end it prints a table. **Paste that table into the chat.** You can stop early with `Ctrl+C`
(it still prints the summary). Data goes to `~/desk-data/` (never into the code folder).

### Other commands
```bash
uv run desk status           # provider health, last collection run, database sizes
uv run desk config           # every setting and the configuration "snapshot hash"
uv run desk db check         # database integrity check
uv run desk db backup        # consistent copies into ~/desk-data/backups/
uv run desk doctor --offline # machine checks only, no network
```

### M2: discovery and safety

Your existing free keys in `~/.config/desk/secrets.env` are reused. No new key creation is needed if the keyed
doctor already passed. Update dependencies with `uv sync --frozen`; databases upgrade automatically without
resetting your collected data.

```bash
uv run desk discover --minutes 1440          # 24 hours, read-only; no wallet or trades
uv run desk discovery-report --require-hours 24
uv run desk console                         # optional read-only status console, second terminal
```

The duration check exits nonzero for a short or unfinished run. To check a specific recorded run, add
`--run-id RUN_ID` alongside `--require-hours 24`. Short cloud smoke tests are recorded separately and do not
close M2. See [M2_HANDOFF.md](M2_HANDOFF.md) for checks, supported security coverage and the next milestone.

Discovery is bounded by configured provider budgets and two expensive assessments per cycle. Unknown authority,
holder, liquidity, Shield or quote evidence never produces a security pass. Token identities, pool identities,
creation timestamps and source claims remain separate. The early console is read-only; the full React dashboard
is M7.

### M3: SHADOW research and replay

M3 reads existing collection records without making additional provider calls. It records feature snapshots,
immutable strategy specifications, research signal rankings and NO_TRADE outcomes. It never creates positions.

```bash
uv run desk shadow --minutes 360
uv run desk shadow-report
uv run desk replay --require-hours 6
```

The unchanged replay uses stored inputs and specifications; copied threshold experiments are written separately
to the append-only research registry. A short or interrupted run cannot satisfy the six-hour acceptance gate.
Cold-start regime history and unverified provider claims remain insufficient; the engine may correctly choose
NO_TRADE throughout. Regime labels need 30 days of market context, which `desk discover` records every minute —
keep it running. See the handoff for the currently missing live evidence and provenance limits.

### M5: PAPER learning book (no wallet, no real money)

```bash
uv run desk paper --minutes 4320     # 72 h: collection + discovery + paper entries; starts the guardian itself
uv run desk paper-report             # results, reconciliation, $100-account replica
uv run desk confidence               # confidence matrix + readiness per strategy (M6)
uv run desk stop                     # kill switch: no new entries (open positions stay supervised)
uv run desk resume                   # clear it (type RESUME)
```

Paper cash is $100,000 but every ticket is sized as your $100 account (10% of $100 + realized paper profit), so
losing streaks don't stop the learning. Up to 10 paper positions at once (free quote budget). The report also shows
what the real $100 account would have done (2 positions, its loss limits). Use your keyed laptop setup.

Every paper trade pays realistic fees: Jupiter's swap fee, pool fees and price impact are already inside each quote
(verified), and three network transactions per round trip (buy, sell, close the token account) are charged from
real Jupiter fee reports once enough exist, or a conservative estimate before that. The moonshot hypothesis H8
looks for rare explosive runs and exits fast; it may use a larger ticket (up to 15%) only after its own paper trades
prove a positive net edge in the confidence report. See PLAN §11.4, §12.3 and §13.1.1.

### Evidence council (research only, never changes a trade)

```bash
uv run desk council report       # what the two research advisors recorded, and what that can and cannot show
```

While `desk paper` runs, two research advisors record every 15 minutes what they would have said about each new
decision (see `research/council/`). They add no provider request and have zero influence. Expect mostly ABSTAIN at
first: the main one needs 76 hours of unbroken 15-minute history for a token. Turn recording off with
`council = "off"` under `[research]` in the config.

### M9: readiness report (the signal for considering real money)

```bash
uv run desk report readiness     # writes ~/desk-data/reports/READINESS_REPORT.md and prints the verdict
```

The report gives exactly one verdict — INSUFFICIENT EVIDENCE, FAILED VALIDATION, CONTINUE PAPER TESTING or
ELIGIBLE FOR MANUAL LIVE REVIEW — from recorded data only, with the forward period and collection gaps, the
observed/conservative/stress results, the $100-account replica, every strategy's confidence matrix, the unresolved
risks, the disclosures and the query behind every figure. While `desk paper` runs it is rewritten every 6 hours and
the verdict appears on the dashboard (Overview and Research). ELIGIBLE only permits your review; it changes nothing.

### M8: real money (never used until you decide)

```bash
uv run desk live keygen          # creates YOUR hot wallet locally; prints only the public address
uv run desk live validate        # zero-cost check of the whole order → inspect → simulate path; sends nothing
uv run desk live status          # the seven LIVE gates and which are missing
```

LIVE stays impossible until all seven gates hold: `desk.mode = "LIVE"` plus `DESK_LIVE_CONFIRM=yes`, a typed
`desk live approve <strategy version>`, a readiness verdict of ELIGIBLE FOR MANUAL LIVE REVIEW at most seven days
old, your own `live.max_capital_sol` (> 0, ceiling 2 SOL), the signer armed with your passphrase (`desk live arm`),
fresh preflight checks and a calibrated market regime. The signer re-checks all seven for every BUY immediately
before signing. When you decide, the steps are in [docs/RUNBOOK.md](docs/RUNBOOK.md) ("Going LIVE"):

```bash
uv run desk live arm             # terminal 1: unlock the signer (type the passphrase here, nowhere else)
uv run desk live run             # terminal 2: PAPER keeps learning; LIVE decides on the same candidates
```

Both terminals need `DESK_LIVE_CONFIRM=yes` and the private configuration copy (`DESK_CONFIG_DIR`) described there.

`desk live run` also starts the independent LIVE guardian, which exits LIVE positions through the signer. LIVE
tickets are 10% of min(equity, allocation), so the 0.05 SOL minimum ticket needs at least 0.5 SOL of allocation.
LIVE never compounds by itself: it grows only when you raise `live.max_capital_sol`. Fund the hot wallet with the
allocation plus the 0.05 SOL reserve plus about 0.01 SOL for fees. Never paste the passphrase or any key into a chat.

The PAPER learning book's profit-based growth is research; it does not authorize a larger real trade.

### M7: local dashboard and operations

```bash
uv run desk dashboard                # open http://127.0.0.1:8765 in Firefox
uv run desk backups run              # online backups; existing ledgers only
uv run desk up                      # opt-in systemd user dashboard and backup timer
uv run desk up --paper               # also start independent guardian and PAPER engine
uv run desk down                     # stop engine/dashboard; guardian continues supervising
```

The **Paper trading** tab (`g` then `t`) shows what the paper book is doing in plain English: every look, skip,
buy and sell with its reason, open positions with their stop, trailing stop and time limit, closed trades after
fees, the signal-to-trade funnel, fees, the moonshot watch and the $100-account replica.

Built UI assets ship with the repository, so running the dashboard needs Python only. To rebuild UI source,
run `scripts/build_ui.sh` with Node/npm or Docker installed. No remote fonts, token images, or chart CDN are used.
Controls cannot arm LIVE or resume stopped entries; `desk resume` remains a terminal confirmation.
Exports include the latest 10,000 positions. See [the runbook](docs/RUNBOOK.md) for service and restore details.

---

## The Robinhood desk (separate)

A second desk for the liquid crypto Robinhood lists, with its own databases, PAPER book and dashboard on port 8766.
Same discipline: frozen rules, Robinhood's own prices for every paper fill, baselines to beat, no AI judge, and no
order code at all. Setup and the first historical results: [docs/ROBINHOOD.md](docs/ROBINHOOD.md).

```bash
uv run desk rh keygen     # once; paste the printed PUBLIC key into Robinhood (read-only actions)
uv run desk rh paper      # terminal 1
uv run desk rh dashboard  # terminal 2 → http://127.0.0.1:8766
```

## Free API keys (recommended before M2)

Both are free. **Never paste a key into the chat** — they go only into a private file on your laptop.

1. **Jupiter API key** (raises the quote/token limit from ≈30 to 60 requests per minute):
   go to <https://developers.jup.ag/portal>, sign in, create an API key on the free plan, copy it.
2. **Solana RPC URL** (needed for token-holder checks; the public endpoint refuses them):
   sign up for a free account at <https://www.helius.dev>, open the dashboard, copy your **mainnet RPC URL**
   (it looks like `https://mainnet.helius-rpc.com/?api-key=...`).
3. Create the private file:
   ```bash
   mkdir -p ~/.config/desk
   nano ~/.config/desk/secrets.env
   ```
   Put exactly these two lines in it (with your values), then save with `Ctrl+O`, `Enter`, and exit with `Ctrl+X`:
   ```
   JUPITER_API_KEY=your-jupiter-key-here
   SOLANA_RPC_URL=https://mainnet.helius-rpc.com/?api-key=your-helius-key-here
   ```
   Then make it private (only your user can read it):
   ```bash
   chmod 600 ~/.config/desk/secrets.env
   uv run desk doctor     # should now say "configured (value hidden)" and the holder-list check should pass
   ```

---

## Important
- Profitability is **not** promised. A likely and acceptable outcome is "no defensible edge — remain in PAPER".
- Never paste a seed phrase or private key into any chat, web form, or file in this repository.
- Developers: run the checks with `uv run pytest` (offline), `uv run pytest -m live` (read-only network tests),
  `uv run ruff check src tests`, and `uv run mypy`.
