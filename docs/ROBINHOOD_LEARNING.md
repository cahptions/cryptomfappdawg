# The Robinhood desk's learning lab

Operator request (2026-10-20): "the app should be learning to trade by training while it's gaining the data … our
ultimate goal is for it to learn and produce successful profitable trades." This is how the desk learns without
fooling itself. Code: `src/desk/rh/learn.py` (the lab), `src/desk/rh/learning.py` (running it in the desk).

## Why not "learn from each paper trade"

A $100 paper account makes a few trades a week. Telling skill from luck at that rate takes years. The coins
Robinhood lists, though, have years of daily price history, so the lab learns on that history and uses the paper
book to confirm.

## How it learns

1. **Variants:** 255 rule variants in four families, plus a statistical model.
   - TREND: breakouts over 10/20/55 days with average, exit and trailing-stop settings.
   - XMOM: relative momentum over 7–91 days, top 1/3/5, rebalanced weekly or every 4 weeks.
   - REVERT: 2-day RSI dips in an uptrend.
   - TIMING: hold BTC, BTC+ETH or the 5 most-traded coins while above the 50/100/200-day average.
   - The model: L2 logistic regression on 15 daily features, refitted every month.
2. **Simulated honestly:** every variant trades over up to 4 years of closed daily Coinbase bars.
   - It decides at a close, buys at the next open, and checks the 2×ATR stop (clamped to 5–25%) intraday.
   - It pays the coin's **measured Robinhood round trip**, or 2% until measured.
   - Each trade is compared with BTC over the very same days.
3. **Graded only on unseen months (walk-forward):** at the start of each month, a procedure chooses using only
   trades whose outcome was already known in the previous 365 days. It then trades the next 30 days it has not
   seen. The record that counts is the chain of those unseen months. Three procedures compete:
   - the best single variant;
   - the best three;
   - the model.
4. **Promotion rule (frozen):** a procedure passes only when all of these hold. The best passing procedure is
   promoted:
   - at least 12 unseen months and 60 unseen trades;
   - profitable after costs, p < 0.05;
   - better than holding BTC over the same days, p < 0.10.

   Both p-values are one-sided stationary-bootstrap tests, Holm-adjusted across the three procedures.
5. **PAPER only:** with `robinhood.learning = "paper"` (default), the promoted procedure trades the PAPER book as
   `L1_LEARNED`.
   - It holds at most 4 positions, with the desk's normal tickets, cost ceiling and conservative fills.
   - Each position is versioned by the run's selection hash and exits by the rule that opened it.
   - `record` learns and reports only; `off` does nothing.
   - The signer and LIVE code cannot import the lab (tested).
6. **Keeps learning:** the lab re-runs whenever a month of new data has arrived (also at the first start, and
   whenever the lab itself changes). Robinhood-measured costs replace the 2% assumption coin by coin as the
   desk probes them.
7. **Forward check:** if the current choice's own closed paper trades, at least 20, have a 90% upper bound on
   their mean below zero, it is **benched**: no new entries until a later run.

The Learning page on the Robinhood dashboard shows everything:
- the verdict;
- each procedure's record on unseen months against BTC;
- today's choice;
- the past year's leaderboard;
- the learned strategy's own paper record and past runs.

`uv run desk rh learn` runs it on demand (`--no-fetch` uses stored history; `--json` for the full record).

## What it found first (2026-10-20, evidence `research/evidence/2026-10-20/rh_learning_lab_v2_starter20.json`)

On 20 starter coins, 4 years, 30 unseen months, 2% round trip:

| Procedure | Unseen trades | Wins | Avg per trade after costs | vs BTC same days | Verdict |
|---|---|---|---|---|---|
| Best single variant each month | 85 | 15 | −2.62% | −2.92% | not yet |
| Best three each month | 109 | 22 | +0.32% | +0.06% | not yet (not significant) |
| Model, retrained monthly | 46 | 13 | −2.81% | −3.86% | not yet |

Nothing was promoted. Choosing last year's winner did not carry into the next month. At these costs, only the
"best three" procedure broke even. That is the lab doing its job: it does not risk the book on something history
says does not work. The laptop run will use all of Robinhood's ~90 coins and measured costs, and repeats monthly.

## How it was changed, honestly

- **v1:** TREND, XMOM and REVERT plus the model. Nothing passed at a 2% round trip.
- **v2, added before its results were seen:**
  - The slow, low-turnover TIMING family and 4-week XMOM, because costs dominated.
  - A selection score that can choose slow variants: total earned minus one standard error, with at least 3
    trades. v1 needed 15 trades a year, which locked TIMING out.

Both versions are recorded through `lab_sha256`. Further changes after seeing results are fitting the past and
belong in a new, versioned lab with that disclosed.

## Limits

- **Survivorship:** the universe is today's Robinhood list, so coins that died are missing.
- **Fills:** daily open fills ignore size and time of day.
- **Literature already saw this history:** the families come from published research.
- **Forward evidence still required:** a pass is not proof of future profit. Forward PAPER results on
  Robinhood's prices remain the evidence that counts, and real money needs the operator's own decision and the
  LIVE gates.
