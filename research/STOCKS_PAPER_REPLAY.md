# Visual PAPER and historical stock strategy replay

## Operator workflow

Open Paper. Live PAPER shows separately funded strategy accounts, account-value charts, open and closed trade
cards, pending orders and the decision timeline. Click a position for quantities/legs, entry and exit costs,
P/L, stops and its original selection reason. Filter by symbol and page through the real ledger. The equity
chart reads the complete account history, including histories longer than the former 400-session report cap.
Use pointer inspection or arrow/Home/End keys. These views do not place brokerage orders.

Historical backtests is a separate tab. Choose 63, 126 or 252 trading sessions, start or cancel a replay, and
inspect its strategy accounts, trades, equity curves, held-out checks and assumptions. Errors and progress
appear in the app. A failed or cancelled run preserves the last completed report. No terminal work is needed.

Automatic replay is enabled by default and starts once enough bulk history is saved: the chosen window plus
60 earlier warm-up sessions. It refreshes once per newly stored daily close, not on each page refresh. A
failed attempt does not restart in a tight loop; the operator can retry in the GUI. PAPER Pause/STOP also
stops an owned automatic replay. Manual offline replays are separate from PAPER automation. Evidence
evaluation and historical replay are serialized to avoid competing analysis jobs.

The 63/126-session evidence grades shown for forward PAPER measure accumulated observations. They are not
waiting periods before an otherwise eligible strategy can trade. Reconstructed sessions never increase the
forward-evidence counter.

## Chronology and accounting

The replay reads a consistent read-only snapshot of stored market history. Research and account databases
are new in-memory databases. It does not call providers or write to the source, current research or forward
ledger. Results and private preferences have separate stocks-backtest files. Cancellation signals only the
exact owned child process; it does not trust a browser-supplied command or PID.

Each daily panel excludes later bars and splits that had not executed by that date. Signals are generated
after that session, then fill at the next available opening price through the existing stock PAPER engine.
Execution uses prior-session liquidity, the configured spread/slippage/fee model, position limits, exits,
stops and corporate actions. Open positions remain marked at the final date. Each strategy and benchmark
has independent capital; their balances are not one combined portfolio. The benchmark uses its simulated
account including the engine's execution and corporate-action accounting.

Fixed documented committee weights are used. Current learned weights, social/options observations and
non-vintage macro/short-interest inputs are excluded. Squeeze-runner rules are excluded because the stored
short-interest source does not establish a dated publication archive. Dated filing/fundamental records are
used where available. The input settings and strategy hashes are retained with each report.

Walk-forward checks rank candidates using an expanding prior training window, then evaluate the selected
existing simulated strategy account over the next non-overlapping block of up to 21 sessions. This is a
comparison of independently funded accounts, not a cost-free switching portfolio. It never promotes weights,
rewrites earlier decisions or establishes a profitable edge merely by selecting the best prior return.

## Limits and resources

Stored/current listing coverage creates survivorship risk: delisted companies may be missing. Available SEC
facts may not preserve every restatement or revision. These limitations are shown in the GUI. Historical
options strategy replay requires archived timestamped bid/ask chains; stock candles cannot supply these.
The requested replay window is bounded to 252 sessions. Broad-universe facts are checked for memory pressure
during analysis. The child runs at reduced scheduling priority with single-threaded numerical libraries.

Statistics include all trades. The saved detail view retains the latest 500 positions across strategies,
200 decisions and 200 pending orders. Skipped sessions are disclosed. Reports larger than 4 MB fail with a
human-readable message while preserving the previous completed report. Live forward positions are separately
paged directly from the ledger and retain the original entry reason.

## Verification

Offline tests cover a real chronological replay with trade generation, read-only source/forward isolation,
future bar/volume/split perturbations, time-limited panels, held-out selection, owned worker lifecycle,
automatic once-per-close scheduling, Pause/resume behavior, CSRF and paged account-scoped read APIs. The equity
API test includes 405 sessions. Browser fixtures contain engine-produced synthetic fills and stops, with no
private provider access. Desktop/mobile flows exercise charts, cards, details, closed P/L, decisions and
replay start/cancel/completion. Verification on 2026-10-23: 1,297 offline tests passed, 5 opt-in live tests
deselected; targeted replay tests passed again after preserving older positions' original entry reasons.
Ruff, mypy (48 Stocks files), JavaScript syntax and diff checks passed. All four desktop/mobile workflows
passed: empty data, populated data, chart/calculator/Evidence and visual PAPER/backtests. No operator's
private Schwab or Massive access was available, so live entitlement and provider completeness remain unverified.
