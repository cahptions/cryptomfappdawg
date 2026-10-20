# Operator commands and durable continuation

Branch: `codex/early-opportunity-research`, based on Claude `9c255ff`. Last fetched Claude head remained
`9c255ff` during final review. This version includes that branch's work; it does not replace it with the
older separate LIVE prototype. Do not merge `/workspace/cryptomfappdawg`'s uncommitted LIVE work.

## Get the version on the laptop

These commands run **on the Linux Mint laptop**, in Terminal. Runtime data remains in `~/desk-data`, outside
the checkout. No download link/patch file is needed. If a timed acceptance collector is still running, let
that run finish before updating its working folder; research does not require stopping or restarting it.

At a normal update/restart point:

```bash
cd ~/kestrel
git status --short
git fetch origin
git merge --ff-only origin/codex/early-opportunity-research
uv sync --frozen
```

If Git refuses because of local edits or divergent commits, stop at that message and preserve those changes;
do not use `reset --hard`, overwrite the configuration, or delete databases. The cloud cannot know unpublished
laptop changes. This branch was verified against the stated remote Claude branch, not an unseen local branch.

Check the two configured bases (does not read credentials):

```bash
uv run python -c 'from desk.core.paths import DeskPaths; from desk.core.config import load_config; s=load_config(DeskPaths.from_env().config_dir).settings; print("Solana reference:", s.paper.starting_capital_usd); print("Robinhood reference:", s.robinhood.starting_capital_usd)'
```

Both should print500. A custom `DESK_CONFIG_DIR` may still hold an operator override; update the two
`starting_capital_usd` settings there deliberately rather than replacing that whole file.

## Start Robinhood PAPER and its dashboard

Use the existing read-only credentials; do not regenerate keys, paste them here or fund a wallet. If the
updated PAPER process is already running, do not start a second copy. To adopt new code/config, use its normal
restart procedure at a suitable stopping point. A config edit cannot hot-patch an already running process.

Terminal1:

```bash
cd ~/kestrel
uv run desk rh paper
```

Terminal2, if the dashboard is not already running:

```bash
cd ~/kestrel
uv run desk rh dashboard
```

Open `http://127.0.0.1:8766`. The account reference is$500, with normal starting tickets about$50. Existing
historical $10 positions remain their original size. The chart is explicitly a current-reference projection
of actual PAPER P&L, not a claim that earlier trades were$50. Learning-book cash remains$100,000. The$1000
absolute learning-loss halt, native SOL fee/probe units and percentage caps were not multiplied by five.

Solana PAPER adopts the new reference at its next normal engine start without deleting/redepositing cash,
using the original recorded SOL/USD conversion rate for the reference basis. Its actual new trade still needs
a fresh market rate, liquidity check and exact-size cost quote. LIVE allocation and authorization are unchanged.

## Run the new research now, without a24h wait

Terminal3, one bounded pass (existing market collector continues):

```bash
cd ~/kestrel
uv run desk opportunities run --venue robinhood
uv run desk opportunities report --venue robinhood
```

For automatic minute-interval research for the next24h:

```bash
uv run desk opportunities run --venue robinhood --minutes 1440
```

Leave that terminal open; Ctrl+C stops only this optional research sidecar. Its report updates the Robinhood
**Learning** page. No import step or completed24h sample is needed. Observations appear as processed; later
outcomes become eligible after15m/1h/6h/24h plus90s. Restarting is idempotent; it resumes from a stored cursor.
An empty/missing-outcome report is honest and does not stop PAPER. A growing source backlog means another
bounded pass is needed; it is not proof that the collector lost data. Closing/sleeping the laptop can leave
gaps that stay gaps. This is not acceptance of uninterrupted operation.

Optional separate Solana track, using its existing local market.db:

```bash
uv run desk opportunities run --venue both --minutes 1440 --outcomes 400
```

Do not start this concurrently with another `opportunities run`; the worker lock intentionally allows one
sidecar. Stop its existing terminal first if changing venues. Missing Solana data is reported, not created.
No extra API calls or credentials are needed by either sidecar. Existing collectors are still responsible
for gathering market observations; the sidecar cannot collect data while the laptop is asleep/offline.

Machine-readable report / storage check:

```bash
uv run desk opportunities report --venue robinhood --json
du -h ~/desk-data/db/research.db
```

The new read-only dashboard endpoint is `/api/rh/opportunities`, backed by
`~/desk-data/reports/opportunities-robinhood.json`. Full immutable records live in research.db. The Solana
report is `opportunities-solana.json`. Group summaries cap at latest10000outcomes and disclose truncation.
Reports record REPLAY vs PROSPECTIVE_SHADOW counts, missing outcomes and quote coverage; none estimates profit.

Keep the laptop awake with its screen off, in another terminal if needed (Mint/X11):

```bash
systemd-inhibit --what=sleep:handle-lid-switch --mode=block --why="KESTREL running" bash -c 'xset dpms force off; exec sleep infinity'
```

Keep it plugged in; Ctrl+C restores normal sleep. This command blocks lid/sleep only while it remains running.
If a timed run already had a sleep gap, keeping it awake now does not erase that gap or manufacture data.

## Verified scope and remaining work

See [verification.json](verification.json) for exact checks and [benchmark.json](benchmark.json) for the
offline synthetic workload. Sources, honest access scope and hypotheses are in [README.md](README.md),
[sources.json](sources.json), [specifications.json](specifications.json), and [VALIDATION.md](VALIDATION.md).
No empirical paper replication, laptop run, prospective edge or timed acceptance is claimed.

Keep working in this order:

1. Inspect the laptop's actual report coverage and backlog, never secrets. Retain excluded/dead/missing names.
2. Measure source receipt lag, usable lead time, original-quantity quote coverage and current filter exclusions.
3. Add a capital/exposure-matched complete-policy simulator and archived costs; source scans may need a
   separately reviewed receipt-time index or per-asset cursor at larger history sizes. Do not migrate a source
   database from the research sidecar or change a running collector to hide this limitation.
4. Freeze the final policy and untouched forward period. Dependence-aware incremental net evaluation is required.
5. Promote only a separately validated PAPER filtering role, then evaluate ranking and sizing independently.

Preserve STOP, guardian, security, executable-size checks, account-relative caps and LIVE authorization.
Do not turn WATCH into a BUY, assign it an uncalibrated probability, or count the two venue versions as two
independent confirmations. No automatic promoter, funds transfer, real order or changed production entry
rule was added. All fuller trade-policy/exit experiments in README are proposals, not delivered trading code.
