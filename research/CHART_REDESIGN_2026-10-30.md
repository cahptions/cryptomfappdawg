# Chart and calculator review — 2026-10-30

Stocks-only chart overhaul on top of `7ec7261`. No real orders, strategy promotion changes, or LIVE crypto changes.

| Surface | Issue found | Upgrade |
|---|---|---|
| Stock price / candles | Separate hover, scaling and mobile code; no usable value axis | Shared responsive chart; source-aware ranges, OHLCV tooltip, previous close, keyboard/touch, zoom, retained real OHLC aggregation |
| PAPER / historical replay | Limited inspection, sparse history looked like a populated long-range chart | Account/P&L modes, labeled value/time axes, available session ranges, retained-observation dots, normalized SPY, trade markers; replay stops at selected session |
| Learning | Only the last 1,000 P/L rows; no axes or account context | Read-only retained-history endpoint; real extrema-preserving sampling, dated cash/positions/fees/funding, all-time P/L drawdown, metric/range/zoom controls, trade/funding markers, last market-source timestamp |
| Plays | Duplicated chart code, dense mobile controls | Shared quote/estimate graph, best observed exit annotation/jump, quantities and entry basis preserved, dated sources and gaps retained |
| Performance / calibration | Inconsistent axes and pointer support | Shared multiseries comparisons, optional baselines, keyboard/touch/tooltips, sample counts, labeled probability axes |
| Calculator | Tiny plot, huge price range, no chart tooltip | Chart-first workspace, focused/wide price view, price/date/IV sliders, multi-leg editing, after-fee scenario graph, expiry comparison, zero/spot/break-even guides, clickable price/time P/L grid |
| List sparklines | Inconsistent renderers and loss colors | Shared gap-aware sparks; modeled Play segments stay dashed |
| Financials / earnings / dividends / insiders | Different tooltip and axis styling | Retain grouped columns and EPS dots (appropriate to discrete reporting periods); align tooltips, labels and grid styling with the chart system |

## Data contract

Schwab remains first for quotes, minute candles and option chains through the existing collector. Read-only chart requests never contact the broker or fabricate marks. PAPER account curves are saved ledger observations; calculator curves and heatmap cells are explicitly modeled from saved bid/ask/IV assumptions. Archived play entry references remain archived references. Missing observations break paths, one observation is a dot, and ranges never create extra history.

Learning has independent uncapped/$500k/$1k/$5k accounts. Cash, contributions, realized/unrealized P/L and fees at a selected historical mark come from dated ledger entries, never the current account totals. Funding is excluded from trading P/L. Sampling retains actual first/last/P&L/equity extrema per bucket; markers and SVG payloads are bounded while the underlying ledger remains intact.

UI protocol is 8: update and fully restart the Stocks server so HTML/assets/API agree. Runtime/assets are captured at server startup, including in browser fixtures; restart fixtures after static changes.

## Verification

**Complete:** 1,502 offline tests passed (5 live opt-in tests deselected). Ruff, strict mypy across 247 files, all Stocks JS syntax and pinned Prettier 3.3.3 passed. Nineteen relevant backend tests passed after final refinements.

Backend tests cover dated accounting, funding isolation, full-history sampling/extrema, prior-period drawdown, empty/invalid requests and exact price/time-grid agreement with after-fee scenarios. Browser checks cover desktop and 375px touch/keyboard interactions, gaps, chart state, calculator legs/scenarios, Plays best exits, PAPER replay and mobile overflow. All ten desktop/375px workflows passed: shared chart edge cases, GUI/calculator/Evidence, stock profile, intraday, PAPER/replay (alone), sparse PAPER/polling, Learning, retail Learning, Plays history and history freshness. The new chart workflow reports no console errors or horizontal overflow.

Authenticated Schwab/laptop verification is unavailable in this cloud session. Synthetic fixtures verify rendering/data contracts without claiming real-key entitlement or freshness.
