# Updating Stocks without mismatched pages and server routes

Updating the repository does not replace a running Python process. Before this fix, the server read JavaScript
and HTML directly from disk on each request. A git pull could therefore display new Stocks/Evidence controls
against older Python handlers, yielding plain `Not Found` responses for `/api/stocks/explore`,
`/api/stocks/proof/start` and `/api/stocks/proof/health`. Refreshing the browser did not restart Python.

The Stocks application icon now compares the running server's source revision with the installed code before
reusing it. If they differ, or the older server lacks the runtime endpoint, it gracefully restarts only the
current user's exact Stocks dashboard lock owner. The existing pidfd and command-identity checks remain in
force. It does not kill processes by a name, delete locks, force-kill unrelated workers, or enable paused PAPER.
The direct `desk stocks dashboard --open` command also checks before reusing a running dashboard.

Each new server snapshots its HTML/JavaScript/CSS at startup so a later pull cannot give it an incompatible
interface. `/api/stocks/runtime` exposes only application/protocol identity, source hashes and whether a restart
is needed. No paths, credentials or private data are included. The startup session carries the UI protocol;
new pages loaded from a pre-fix server give restart guidance before calling unsupported actions. Plain route
404s also explain the mismatch, while normal missing-stock errors retain their specific meaning.

After installing this version, reopening the Stocks application icon automatically checks and loads updates.
For the first recovery from an already-running older server, the explicit update/restart command is:

```bash
cd ~/kestrel &&
git pull --ff-only origin codex/arbitrage-v1 &&
uv sync --frozen &&
uv run desk open --desk stocks --restart
```

This preserves the data directory, private connection settings, forward PAPER ledger and automation preference.
An independently running collector is not stopped by a process-name search. If ownership cannot be verified,
the restart refuses to signal it and reports that no process was stopped.

Regression checks cover a real earlier-installation dashboard replaced through the application icon path,
unchanged saved PAPER/paused state, frozen assets through an in-place update, missing/invalid runtime metadata,
current-server reuse, and rejection of non-dashboard lock owners. Browser checks reproduce an old session and
both missing Evidence actions at desktop and phone widths, then verify the current pages after reload.

Verification: 1,310 offline tests passed (5 opt-in live tests deselected), Ruff and mypy (50 source files) passed,
and desktop/mobile workflows passed for update recovery, populated/empty pages, chart/calculator/Evidence and
visual PAPER/backtests. These checks use isolated synthetic data and owned temporary dashboard processes.

Maintainers: when a future UI needs an incompatible server contract, bump UI_PROTOCOL and the matching startup
check in stocks.js together. Runtime revision comparison also catches source changes within a protocol version.
