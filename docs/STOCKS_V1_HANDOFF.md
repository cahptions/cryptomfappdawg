# Continue KESTREL Stocks v1.0

Read the [product and experiment blueprint](STOCKS_V1_BLUEPRINT.md), then the [primary-source research review](STOCKS_V1_RESEARCH.md). The operator requested the idea and a build-ready handoff for another model. The regular Dell branch contains the design. Partially written application changes are preserved separately on the remote recovery branch.

## Prompt to paste into the next coding session

> Build KESTREL Stocks v1.0 from docs/STOCKS_V1_BLUEPRINT.md and docs/STOCKS_V1_RESEARCH.md. Start by fetching both the normal Stocks branch and the named recovery branch, inspecting local edits, and reading PROGRESS.md and this handoff. Preserve every existing edit. Use the recovered code as prototypes and review each against the design and the current head. Work only on Stocks research and simulated PAPER.
>
> Implement the blueprint in independently shippable slices. Begin with the deep-linked “Why this book waited” view for a real $1K or $5K PAPER decision. Reconstruct it from the account's own ledger, frozen policy, decision-time cash when available, and timestamped evidence. A policy wait can exist with no order. Show exactly why and what could change it. Verify historical stability after a deposit, restart and late receipt. Show which checks passed and which require the Dell. Complete this slice before wiring the bounded research collector or enrolling a prospective study.
>
> Progress toward the full blueprint. For every new signal or model name its comparator, prospective metric, evidence class, marginal cost and losing condition. Save and push recoverable checkpoints while working. Publish verified slices to the established Dell branch. Update PROGRESS.md and give the operator the exact update command with each release. A chat reset must be recoverable from Git and the handoff alone.

## Fetch and inspect without touching local edits

From the Dell checkout or a fresh clone with access to the same remote:

```bash
cd ~/kestrel &&
git fetch origin refs/heads/claude/funny-rubin-gq99vj:refs/remotes/origin/claude/funny-rubin-gq99vj refs/heads/codex/stocks-v1-recovery:refs/remotes/origin/codex/stocks-v1-recovery &&
git status --short --branch &&
git log -1 --oneline origin/claude/funny-rubin-gq99vj &&
git log -1 --oneline origin/codex/stocks-v1-recovery &&
git diff --stat d4cb73f origin/codex/stocks-v1-recovery -- src tests
```

Keep the operator's checkout in place. Build in a separate worktree from the current regular Stocks head. Read prototypes with `git show origin/codex/stocks-v1-recovery:src/desk/stocks/learning_transfer.py`. Review selected diffs before carrying them into the new worktree. The rollback point for each future slice is its preceding verified application commit. Additive migrations must remain readable on restart.

## What exists

The last application release when the design work started was `d4cb73f`. Claude's `5cc9092` and the earlier `5bdcaa3` are historical checkpoints. The recovery branch contains unverified code written before the operator narrowed this turn to design.

| Area | Recovery prototype | Remaining work |
| --- | --- | --- |
| Learning | `src/desk/stocks/learning_transfer.py`, Learning client, read-only transfer-review web handler | Review joins from frozen policy through partial fills to fee-inclusive cash. Add decision-level deep links, policy waits with no order, legacy-data handling and independent book reconciliation. |
| Plays | `play_comparison.py`, `static/play_comparison.js`, Plays and execution view edits | Verify persistent shortlist, normal/archive/horizon routing, differing expirations, cash comparison and whole-unit scenarios in both books. Trace UI requests to the actual handler. |
| Exit quotes | `exit_quotes.py`, `play_tracking.py`, `play_history.py`, research migration 0026 | Verify nullable legacy paths, all legs at a common receipt, side sizes, retention budget, folding before/after restart and duplicate resistance. |
| Forward science | `brain_study.py` and research migration 0027 | The recovered Python defines feature, decision and grading helpers. Review timestamps, dividends/borrow, calendar, dependence and statistical power. Implement the public summary, runner integration and restart tests before enrollment. |
| Research Brain and allocation | The blueprint, existing `runner.py`, `collect.py`, `activity.py`, `option_signals.py`, `learner/forward.py` | Central decision screen, decision-boundary evaluations, matched acquisition experiment, historical reconciliation and operator acceptance remain design work. |

The existing production ledger, small-book refills, historical ablations, forward call-spread comparison and PAPER recovery already shipped in earlier work. Review current source before duplicating any of them.

## Verification inherited by the next session

- **Synthetic startup and restart**. Session API 2.61s, first PAPER ledger commit 7.61s, 25 accounts; pause/start preserved rows, and SQLite integrity and foreign keys passed. The browser fixture rendered Home, Plays, Learning, Learner and Paper at 1366px and 375px. This predates the assembled prototypes.
- **Offline run during development**. 974 passed and two failures. Both failures were in `tests/integration/test_stocks_plays.py`. The first category's plays list was empty in `test_the_page_endpoint_serves_the_saved_plays` and `test_a_full_days_board_over_four_megabytes_is_served_compressed`. Root cause remains unverified. Reproduce in an isolated worktree at `d4cb73f`, then on the proposed slice. Inspect whether the fixture's quote dates satisfy selection. Keep production freshness rules intact.
- **Paired reliability and Schwab checks**. An additional offline fault-injection/Schwab run passed 80 checks. It used disposable data and test responses. Live Schwab session and entitlements on the Dell, real human comprehension, weekly resource costs and untouched outcomes remain open.
- **Real operator evidence**. The November 19 report confirms use of both refills and separately reports available cash, equity, entries and holding policy. The [blueprint](STOCKS_V1_BLUEPRINT.md) records the relevant counts and amounts.

Useful entry commands from the project root:

```bash
uv run pytest -q tests/integration/test_stocks_plays.py -k 'page_endpoint_serves or four_megabytes'
uv run pytest -q tests/fault_injection/test_stocks_reliability.py tests/integration/test_stocks_execution_study.py tests/integration/test_stocks_update_restart.py
uv run desk stocks check-accuracy --json
uv run desk stocks learner-ablate --help
python3 scripts/stocks-retail-status.py
```

Run tests and destructive fault injection only with disposable data. The retail status and accuracy commands read the configured local installation. Preserve source provenance and report real performance only when measured on that installation.

For future **application** releases the established operator command is:

```bash
cd ~/kestrel &&
git pull --ff-only origin claude/funny-rubin-gq99vj &&
uv sync &&
uv run desk stocks restart
```

For the design-only update a fast-forward pull is sufficient. The Markdown can be read directly in the repository or given to another coding model.
