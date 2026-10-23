# Stocks UI/data release checkpoint

Branch: codex/arbitrage-v1. Chart/research release b3f6c75 is published and its remote hash was verified.
The subsequent visual PAPER and historical replay release is implemented and verified, ready for publication.

Published in b3f6c75: OHLC/volume hover tooltip, Stocks explorer, Evidence evaluation GUI, Settings access test,
option capture-session repairs, strike-click multi-leg P/L calculator, genuine 5Y/Max charts, and independent
Schwab-first stock-page quotes/history. Massive remains the bulk unadjusted daily source for research/PAPER.
See STOCKS_UI_AND_HISTORY.md for source boundaries and verification details.

Implemented next: visual strategy accounts, interactive equity history, paged open/closed trade cards, leg/cost/
decision detail, pending orders and decisions; automatic/manual 63/126/252-session chronological stock replays
in isolated books, cancellation/progress, fixed weights, prior-session liquidity and held-out comparisons.
See STOCKS_PAPER_REPLAY.md. Historical option quotes cannot be reconstructed from stock candles.

Verification: 1,297 offline tests passed (5 opt-in live tests deselected); targeted replay tests were rerun after
retaining original reasons on older historical positions. Ruff, mypy (48 Stocks files), JavaScript syntax and
diff checks passed. Desktop/phone flows passed for populated/empty data, visual PAPER, real replay lifecycle,
long chart history, multi-leg calculator and Evidence evaluation. All browser sources and trades are synthetic.

The earlier automatic approval usage-limit interruption resolved. Publication of b3f6c75 was explicitly
approved by the operator after the initial destination/publication review rejection. No review was bypassed.

No operator laptop, private credentials, funding, purchases or brokerage orders were accessed. No LIVE changes.
A branch push does not update the operator's installed or running application.
