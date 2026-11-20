# KESTREL Stocks v1.0 operator and build handoff

The Stocks release covers account-specific decision history, play comparison, historical exit receipts, Learning transfer review and a Research desk with prospective controls. [Product and experiment blueprint](STOCKS_V1_BLUEPRINT.md) · [Research methods and primary papers](STOCKS_V1_RESEARCH.md)

## Update and open the shipped workflows

```bash
cd ~/kestrel &&
git pull --ff-only origin claude/funny-rubin-gq99vj &&
uv sync &&
uv run desk stocks restart
```

- **Learning.** Choose the $1,000 or $5,000 account. Select **Why this book waited** or **View PAPER decision**. That direct link includes the book and decision ID. The Orders tab also links to earlier decisions. **Replay this decision** reveals the action and result after a choice.
- **Plays.** Select **Shortlist** on up to three ideas and use **Compare ideas**. The same percentage shock applies to each idea at its own expiry. Open a recorded historical play and choose a quantity to see quote support and the corresponding spread cost and subsequent 10–11 minute midpoint changes when a matched receipt exists.
- **Research.** Follow either book's decision from **Research desk** on Home. **Start forward enrollment** and **Start quote comparison** enable the experiments for later worker passes. Both default to paused on a fresh installation. The IV enrollment schedule is fixed when the worker registers it. Pausing enrollment preserves the schedule and permits grading earlier cases.
- **Learning transfer.** Open **Did the research transfer to this account?** on a small book. The paired return averages require dated prior evidence and comparable exit horizons. Missing, open, cancelled and unevaluable records remain visible.

## Build and verification record

The verified workflow began from `8a4260f`. `6f2ed18` shipped decision history and `29f91b8` shipped play comparison and exit receipts. The prior unverified work is preserved on `codex/stocks-v1-recovery`. The implementation work was staged on `codex/stocks-v1-build`. User edits in the original workspace were kept in place while the release was built in separate worktrees.

| Workflow | Reproduced with disposable data | Dell evidence still needed |
| --- | --- | --- |
| Decision history | A known policy wait with no order kept the original cash and quote after a later deposit, another quote and application recreation. The other account's URL returned 404. A later fill linked to its own frozen order and book. | Historical coverage varies with the data originally captured. Independently reconcile a sample of real waits, orders and closed positions. |
| Play comparison and exits | A supported two-leg receipt yielded one whole supported exit. Two separate receipts remained incomplete after folding. Retention removed expired packed receipts. Chromium verified shortlist, expiry scenario and both book balances. | Real spread and markout coverage by time of day, settlement edge cases and portfolio-level execution sensitivity. |
| IV forward study | Tests froze a matched pair and ignored a revised feature. A fresh future bar produced a synthetic 200 bp mean paired result. The incomplete sample left its p-value blank. A missed window was counted. | Authenticated feed coverage, untouched matched pairs, regime dependence and the final result after enough calendar time. The 200 bp fixture demonstrates grading behavior. |
| Discretionary quote experiment | The chosen arm stayed fixed on retry. A late, sized receipt for the exact leg cleared the original cash limit in a fixture. A receipt with unknown size left it unresolved. | A candidate appears when an otherwise-qualified play for a small book is close to its cash limit, its decision is fresh and a comparable discretionary symbol is available. Measure data yield and costs once eligible sessions accumulate. |
| PAPER operations | The final managed disposable startup served the session API in 5.49s and committed 25 accounts in 15.48s. Pause/start retained the original ledger rows and integrity. An earlier disposable database also upgraded with matching cash rows and totals. Desktop and mobile checks exercise the app's normal routes. | Repeat on the Dell with its authenticated feeds. Record memory, collection lag, storage and weekly training time and run a seven-day soak. Human task timing and comprehension need operator participation. |

The full offline release suite passed 2,104 tests. Five opt-in provider checks were deselected. Ruff, mypy and Prettier also passed.

The earlier two Plays test failures came from asking a frozen view for fixture quotes before their receipt clock. The corrected tests request published views after the receipts. The production cutoff remains in force.

## Continue from the shipped baseline

1. Collect a read-only Dell report using `python3 scripts/stocks-retail-status.py`. Reconstruct observations, estimates and missing evidence for a sample of real cases, including at least one no-order wait in each book.
2. For acquisition, compare usable chains per 100 observed provider operations, per hour and per retained GiB. The follow-up arm adjudicates the original affordability question. Track a comparable decision outcome for the control arm before claiming improved decisions. Capture provider billing separately.
3. For execution, measure per-intent performance across matched quote assumptions and collect assignment and settlement observations where available. Keep simulated fills, snapshot depth and settlement distinct.
4. For learning, expand rejected-play follow-up from the established matched study and benchmark against cash. Run the existing ablation before proposing a model change. Treat strategy promotion as its own decision after untouched results mature.
5. Measure the four operator tasks on desktop and phone, counterbalance order, and compare error types and task time with the previous app. Record support counts and decide whether to simplify parts of the interface from the results.

From the project root:

```bash
uv run desk stocks check-accuracy --json
uv run desk stocks learner-ablate --source forward --rows 20000
uv run desk stocks learner-bench
uv run pytest -q tests/integration/test_stocks_iv_study.py tests/integration/test_stocks_execution_study.py
```

Pause either research experiment in the Research desk to stop assigning new work. Historical decisions and already recorded outcomes remain queryable. Quote receipts use additive storage and follow existing retention.

For future changes, fetch current branches, inspect local edits and preserve them. Update PROGRESS.md and give the operator the exact Dell update command with each verified slice.
