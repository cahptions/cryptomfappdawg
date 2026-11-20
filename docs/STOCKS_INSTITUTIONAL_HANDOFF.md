# KESTREL Stocks research workspace and portfolio tools

Use Stocks for research and simulated PAPER. The previous Dell release, `1f145e4`, corrected viewport jumps during background updates. Preserve local edits when fetching. The development checkpoints are on `codex/stocks-institutional-recovery`. The older v1 blueprint, research review and operator handoff cover decision receipts, execution studies and provider limits.

- [Product and experiment blueprint](STOCKS_V1_BLUEPRINT.md)
- [Research methods and primary papers](STOCKS_V1_RESEARCH.md)
- [Earlier v1 operator and build handoff](STOCKS_V1_HANDOFF.md)

## What the operator can do

| Workspace | Workflow |
| --- | --- |
| Overview | Switch among stored SPY, QQQ and IWM closes. Open the $1,000 and $5,000 book decisions from the same screen. Follow the top ideas into Plays. |
| PAPER | Pick a strategy from the library. Read its recorded equity, cash, trades and grade. Switch from Forward PAPER to historical replay to inspect decisions for a specific session. |
| Learning | Read ledger equity, cash and trading P/L beside the account's latest decision and transfer review. Open the scoped options stress test directly from a small book. |
| Research and Learner | Review experiment outcomes and the account decisions that provide their context. Learner puts the forward test next to the trading gate and surfaces the two frozen lower bounds when the sample matures. |
| Plays | Open the shortlist from the top of Plays. Expand the option data disclosure for session coverage and model calibration. The stored observations and models also remain available from the idea and Performance views. |
| Portfolio lab | Choose Forward PAPER strategy accounts or a saved historical replay. Select two to eight strategies, set a cash reserve, a maximum invested weight and an entry cost, then run the comparison. |
| Portfolio risk | Choose a $1,000 or $5,000 book. Apply a joint SPY and volatility shock and see each option's contribution. The strategy and tracked-play overlay remains available for inspection across independent books. |

The shared navigation and visual system cover the entire Stocks app, including Stocks profiles, Options, Events, Evidence, Schwab account, System and Settings.

## Reproducing the quantitative results

**Portfolio lab.** `src/desk/stocks/capital.py` aligns exact one-session returns on all selected strategy marks and the SPY comparator before splitting chronologically. A missing mark removes the return into that session and the return from it. For Forward PAPER, deposit and recovered sessions are excluded. Risk weights use a long-only minimum-variance fit with 35% diagonal covariance shrinkage and a per-strategy cap. The fit needs at least 40 matched returns and the later evaluation at least 20. The fixed initial weights drift during evaluation. Equal weight and SPY receive the same cash reserve and one-time allocation cost. Cash earns zero. The comparison shows estimated beta to unblended SPY, maximum drawdown and a paired stationary-bootstrap interval for risk-weighted versus equal-weight daily returns. The results panel shows the date cut, evidence source and hash of the aligned returns and settings.

New replays freeze SPY total returns in their report. Older replays use their saved Hold SPY simulation if available. The forward comparator uses the current market store and can change when there are revisions. Strategies with more than one recorded specification are labeled. Fractions of separately funded strategy accounts are a research tool. The paths compound matched observations and leave unobserved moves out. The amount of evidence needed for a strategy allocation decision depends on the return dependence and how many alternatives were tried. A user-selected chronological split can guide exploration. Strategy and parameter choices made after inspecting it need a separately locked prospective test.

**Portfolio risk.** `src/desk/stocks/risk.py` selects the appropriate ledger before calculating the shock. A small-book stress combines only option positions in the chosen account. It uses stored underlying closes, known implied volatility and full Black–Scholes repricing under a combined beta-linked equity move and parallel IV shift. Repricing is conditional on the inputs and ignores spread widening, smile changes and liquidity. Positions without a whole repriced set of legs stay out of the estimate. Coverage counts and close dates appear in the UI. The equity impact percentage requires ledger equity and complete modeled position coverage. The overlay has no shared equity denominator.

**References.** The shrinkage estimator takes its motivation from covariance regularization. Its fixed design choice here is 35%. Politis and Romano describe the stationary bootstrap used for the paired return difference. [Ledoit and Wolf](https://doi.org/10.1016/S0047-259X(03)00096-4) · [Politis and Romano](https://doi.org/10.1080/01621459.1994.10476870)

## Verification and limits

- Reproduced with synthetic data. Perturbing held-out returns changed the outcome and input fingerprint while leaving the fit and allocations identical. Deposits, recovery sessions and a missing equity mark were excluded. Replay used its saved benchmark. Two different contract counts repriced separately in the small books and reconciled to each book's own equity.
- A completed disposable 63-session backtest supplied 62 matched returns for Momentum and Undervalued, with 42 in the fit and 20 held out. With 20% cash, 60% maximum invested weight and a 10 bp entry cost, the risk-weighted blend returned 5.17%, equal weight 5.24%, the SPY blend 3.77%, and cash 0.00%. The underlying market and fills were synthetic.
- Reproduced with a managed disposable runtime. The session API responded in 3.62s and PAPER committed 25 accounts in 11.65s. Pause and resume kept 25 cash rows, 40 decisions, 25 equity rows and no positions in that fixture. SQLite integrity and foreign-key checks passed.
- The Stocks worker and account viewer share the provider rate budget. The rate limiter now samples its grant time inside its write transaction. The controlled lock test and cross-process budget test passed.
- Chromium exercised background refreshes on 22 routes at 375 and 1366 pixels. Portfolio source switching, result preservation and the two stress scopes passed at 375, 768 and 1366. The Research controls, replay return path, small-account transfer and funding workflows passed at phone and desktop widths.
- Dell evidence pending. Real time to first view, data freshness, memory and weekly training cost, recovery through provider expiry, and portfolio comparison coverage on actual account logs. Hedge-fund readiness would also require locked prospective results net of credible execution costs, independent reconciliation, and operator usability measurements.

Targeted checks and the synthetic browser scenario:

```bash
uv run pytest -q tests/integration/test_stocks_capital.py tests/unit/test_stocks_risk.py
uv run python -m tests.browser.stocks_fixture_server --scenario portfolio --port 8892
# In a second terminal, with Playwright available
KESTREL_STOCKS_URL=http://127.0.0.1:8892 node tests/browser/stocks_portfolio.mjs
```

## Dell update

```bash
cd ~/kestrel &&
git pull --ff-only origin claude/funny-rubin-gq99vj &&
uv sync &&
uv run desk stocks restart
```
