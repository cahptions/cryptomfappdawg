# Solana Quant Desk (working brand: KESTREL)

A locally operated research and trading desk for Solana memecoins that discovers opportunities, rejects unsafe
tokens, paper trades under realistic execution costs, measures whether any strategy has a statistically defensible
net edge — and stays in PAPER mode indefinitely if it doesn't.

> **Status:** M0 approved; **M1 passed**. **M2 discovery and safety implemented; 24-hour acceptance pending** — see
> [PROGRESS.md](PROGRESS.md) for test results, including the successful laptop 60-minute run.
> M3 feature/strategy/replay software and a conditional sizing foundation pass offline checks; live acceptance
> stays separate from development. See [M3_HANDOFF.md](M3_HANDOFF.md).
> Nothing here can trade: there is no wallet code at all yet.
> No AI/LLM and no paid services are used (operator decision).

## Documents
| Document | What it is |
|---|---|
| [PLAN.md](PLAN.md) | Architecture, strategy hypotheses, risk controls, costs, milestones, decisions |
| [RESEARCH.md](RESEARCH.md) | Verified facts about every data source, with evidence tags and source URLs |
| [ACCEPTANCE.md](ACCEPTANCE.md) | Objective tests each milestone must pass |
| [PROGRESS.md](PROGRESS.md) | Current status, measured results, and the exact next step |
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
For the newer implementation branches, follow [M3_HANDOFF.md](M3_HANDOFF.md). An existing collection process
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

Profit-based sizing is a standalone M5/M6 foundation: it compounds realized net profit only with matching, fresh
PAPER confidence evidence, and caps each ticket at 15% of eligible account capital plus the other risk limits.
An optional absolute cap may further tighten older profiles. No runtime trade sizing,
wallet, full PAPER ledger or confidence-matrix producer is connected yet.

---

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
