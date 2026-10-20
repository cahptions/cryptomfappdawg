# The Robinhood desk (KESTREL Crypto)

Current PAPER behavior: [continuous multi-timeframe v2](ROBINHOOD_MULTIFRAME.md).
The daily v1 rules and simulations below remain archived experiments and govern existing v1 positions;
they do not validate the new v2 rules. New R1/R2/R3 entries use closed 15m, 1h, 4h and daily data throughout
the day. The two original baselines remain on their daily schedule.

A second, separate desk for the liquid crypto Robinhood lists, next to the Solana memecoin desk. It has its own
databases, its own PAPER book, its own dashboard (port 8766) and the same discipline: preregistered,
deterministic strategies; PAPER on the venue's own executable prices before any money; recorded evidence against
baselines; no model or AI judge. **PAPER only: the desk contains no code that places, cancels or changes an order.**

Built 2026-10-20 at the operator's request ("extend this to all tradeable crypto on Robinhood, two separate
dashboards, same or better parameters").

## Why KESTREL decides and not Robinhood's AI agent

Robinhood now offers agentic trading: a separate funded account that a third-party AI model (or Robinhood's own
"Robinhood Agents") trades through its Trading MCP server ([newsroom](https://robinhood.com/newsroom/hood-summit-2026/),
[support](https://robinhood.com/us/en/support/articles/trading-with-your-agent/)). That puts a language model in
charge of entries and exits, which this project ruled out (PLAN §15): its choices cannot be tested in advance,
cannot be reproduced, and leave no frozen rule to hold accountable. The better arrangement keeps the deciding in
KESTREL's frozen rules and uses Robinhood only as the venue, through its official Crypto Trading API, signed with
an Ed25519 key generated on the laptop. The agent account's good idea, a separately funded allowance, is kept as
the LIVE plan: when LIVE is ever built, the desk's own allocation limit plays that role.

## Data sources (facts in `research/evidence/2026-10-20/`)

| Need | Source | Notes |
|---|---|---|
| Executable prices | Robinhood Crypto Trading API, `trading.robinhood.com` | Every endpoint needs the signed key. 100 requests/min, bursts of 300; timestamps valid 30 s. The desk budgets 60/min. |
| Exact-size cost | `estimated_price` (v1 and v2) | v1 = market-maker routing: no commission, spread inside the price. v2 = exchange routing: fee tier, 0.95% taker under $10K/month ([fee schedule](https://cdn.robinhood.com/assets/robinhood/legal/rhc-fee-schedule.pdf)). The desk asks both and takes the cheaper. |
| Coin list | `trading_pairs` | Only tradable USD pairs with `is_api_tradable=true`; stablecoins excluded. Until the key works, a labelled 20-coin starter list is used. |
| Price history | Coinbase Exchange public candles (keyless) | Robinhood's API has **no history endpoint**. Bars are bucket START, newest first; the newest bar is still forming and is never stored. History only: never a fill price. |

The signature is `base64(Ed25519(api_key + timestamp + path_with_query + method + body))`. The implementation
reproduces the docs' published example signature (test `test_signature_matches_the_documented_example`).

The v2 best-bid/ask feed aggregates partner exchanges and excludes order size and fees, according to its
`Get Best Price` description in the saved official OpenAPI document. A finite positive bid above the ask is
retained as a **crossed aggregate quote**, with its original sides and signed spread. The doctor and coin page
label it explicitly. Zero, negative, nonnumeric and nonfinite prices still fail validation. Crossed top-of-book
prices do not establish executable profit: PAPER fills and the round-trip ceiling still use exact-quantity
estimated totals, including the route's spread or fees. This fix needs no database migration or new credentials.

## Strategies (v1, frozen before any history was looked at)

The original v1 experiment made decisions once a day after 00:05 UTC from closed daily bars
(D uses bars that ended by D 00:00). This is now the legacy exit/baseline and historical-simulation schedule.
Every strategy except the BTC baseline trades only while BTC closes above its 100-day average, and every position
has a hard stop at 2×ATR(20) below entry, clamped to 5-25%, checked every minute on Robinhood's best bid.

| Id | Rule | Source of the hypothesis |
|---|---|---|
| R1_TREND | close above the prior 20-day high and above the 50-day average; exit at a 10-day low, a 3×ATR trailing stop, or 60 days | time-series momentum and trend following: Moskowitz, Ooi & Pedersen (2012); Hurst, Ooi & Pedersen (2017); in crypto, Liu & Tsyvinski (2021) |
| R2_XMOM | Mondays: hold the 3 coins with the strongest 28-day run (skipping the last day) that also beat BTC | cross-sectional momentum in crypto: Liu, Tsyvinski & Wu (2022) |
| R3_PULLBACK | 2-day RSI below 10 in an uptrend (close and 50-day average above the 100-day); exit on the bounce or after 7 days | short-term reversal: Jegadeesh (1990), Lehmann (1990); RSI(2) rule from Connors |
| B1_HOLD_BTC | buy BTC once and hold | baseline: the market |
| B2_RANDOM | Mondays: a seeded random eligible coin held 7 days, same stop | baseline: luck after costs |

Eligibility: at least 120 days of history with at most 5 missing, and a 30-day median daily dollar volume of at
least $1M. Entry priority when the book is full: B1, B2, R1, R2, R3.

## Money rules (same as the Solana learning book)

* Paper learning cash $100,000 (never binding); tickets sized for the $100 account: 10% of ($100 + realized
  profit), never above 15%. At most 10 open positions; R1 3, R2 3, R3 2, each baseline 1.
* An entry needs Robinhood's own estimate for the exact quantity on both routes, a measured round trip of at most
  2%, and room in the book. After a 2.5 s wait a second estimate is taken and the worse one is booked.
* Exits sell the exact quantity on the better route, with the same second-estimate rule.
* $1,000 of cumulative realized loss halts entries. The global `STOP` file also stops entries; exits continue.
* Every ENTER, EXIT and blocked signal (SKIP, with its reasons) is recorded with a hash; cash and events are
  append-only; a position's entry and a closed position can never change (SQLite triggers).

## What history says (simulation, not evidence)

`desk rh backtest` runs the same strategy code over Coinbase history with a 2% round-trip assumption (replaced by
Robinhood-measured costs per coin once the paper desk has probed them). First run, 2026-10-20, 20 starter coins:

| | 3 years (2023-10-21..2026-10-19) | 1 year (BTC −26%) |
|---|---|---|
| B1 hold BTC, per dollar-year held | +57% | −26% |
| B2 random coin | −171% | −201% |
| R1 trend breakout | **+83%** (72 trades, 33% wins, big winners) | **+36%** |
| R2 weekly momentum | −6% | −236% |
| R3 uptrend pullback | −231% | −225% |

Only R1 beat both baselines in both windows. R2 and R3 lose after a 2% round trip. The rules were not changed after
seeing this: changing them now would fit the past. Caveats: survivorship (today's list), fills at the daily open
with a fixed cost, and hypotheses taken from research that already saw much of this history. Forward PAPER on
Robinhood's prices is the evidence that counts. Full reports: `research/evidence/2026-10-20/rh_backtest_v1_*.json`.

## Learning

The desk keeps learning from years of history and promotes to PAPER only what passes on months it never saw:
see [ROBINHOOD_LEARNING.md](ROBINHOOD_LEARNING.md) and the dashboard's Learning page (`uv run desk rh learn`).

## Commands

```bash
uv run desk rh keygen                 # once: creates ~/.config/desk/robinhood.key, prints the PUBLIC key
uv run desk rh doctor                 # key, account, pairs, a quote, Coinbase history
uv run desk rh backtest --fetch       # download history, simulate 730 days (add --days 1095)
uv run desk rh paper                  # the PAPER engine (Ctrl+C to stop); add --minutes N for a timed run
uv run desk rh dashboard              # http://127.0.0.1:8766 (this laptop only)
uv run desk rh report                 # the $100 account, strategies, last session
```

Setting up the read-only key: `desk rh keygen`, then robinhood.com (web classic) → Account → Crypto → API trading →
add a key with the printed public key and **only the read actions** (accounts, holdings, orders, products, quotes);
put `ROBINHOOD_API_KEY=rh-api-…` in `~/.config/desk/secrets.env`. The private key never leaves the laptop, and a
read-only credential cannot trade even if it were stolen. Without a key the desk still syncs history and records
what each strategy would do (SKIP `NO_ROBINHOOD_PRICE`).

## Storage

`~/desk-data/db/robinhood-market.db` (pairs, quotes, estimates, candles, specs, decisions) and
`robinhood-ledger-paper.db` (cash, positions, events, equity). runtime.db is shared for rate budgets and provider
health. `~/desk-data/reports/rh_backtest.json` is refreshed after each daily session.

## Not built (deliberately)

* LIVE: no order code exists. It would need the same seven gates as the Solana desk, a trading-enabled credential
  and the operator's explicit decision after the readiness evidence.
* Robinhood's perpetual futures (announced with up to 10x leverage): out of scope for capital preservation.
