# KESTREL Stocks institutional upgrade

The operator wants both a complete application redesign and a material upgrade in quantitative workflows. They asked whether a hedge fund would consider KESTREL on par. Keep that assessment evidence-based. Their Stocks scope remains research and simulated PAPER. Preserve existing edits. The last verified Dell release at the start of this work was `1f145e4` on `claude/funny-rubin-gq99vj`. `codex/stocks-institutional-recovery` contains in-progress implementation.

## Recovery procedure

Fetch both refs and inspect local status. Start from the tested Dell head in an isolated worktree, review the recovery diff, and complete API and browser verification before publishing application code to `claude/funny-rubin-gq99vj`. Save recovery checkpoints throughout work.

```bash
git fetch origin claude/funny-rubin-gq99vj codex/stocks-institutional-recovery
git log -1 --oneline origin/claude/funny-rubin-gq99vj
git log -1 --oneline origin/codex/stocks-institutional-recovery
```

## Current implementation

- The new navigation rail and light working surfaces span the whole application. Inter and Instrument Serif are bundled locally. The mobile nav keeps the main routes and adds Portfolio lab and risk.
- `capital.py` and the read-only `/api/stocks/portfolio` endpoint implement aligned single-session returns, recovery/deposit exclusion for forward data, fit and held-out windows, a capped covariance-shrinkage allocation, matched equal-weight and SPY portfolios, cash, estimated market beta and a paired block-bootstrap interval. Add the portfolio UI, verify dates/point-in-time benchmark use, add tests for no fit leakage, missing intervals, invalid constraints and small samples.
- The Home API exposes slim ledger-derived small-book balances and decision links. Build the main market chart, the books and the evidence queue into the app's opening view. The existing intraday scanner, lists, Events and Watchlist belong in secondary Home sections or their current routes.
- Make layout changes for the actual workflows across Stocks, Options, Plays, PAPER, Learning, Research, Learner, Evidence, Risk and Settings. Keep the existing viewport stabilization and browser regressions. Prioritize a clear market/PAPER/Research story and a real workflow on each new screen over decorative elements.
- Portfolio tools are for research. Build book-specific scope for risk and avoid summing independent experimental books as if they shared one pool. Distinguish exposure models, replay, audited PAPER and prospective results. Institutional readiness still needs independent validation and timestamped provider data, robust research provenance, realistic execution, and repeated operator testing.

Finish without inventing a hedge-fund parity claim. Verify at phone and desktop widths and publish the exact update command on every release.

```bash
cd ~/kestrel &&
git pull --ff-only origin claude/funny-rubin-gq99vj &&
uv sync &&
uv run desk stocks restart
```
