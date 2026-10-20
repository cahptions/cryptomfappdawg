# Continuous Robinhood PAPER analysis — v2

New R1/R2/R3 PAPER entries no longer wait until 00:05 UTC. History downloads continue while the engine
checks each minute for a newly closed 15-minute candle. The daily BTC candle supplies the broad regime,
4h candles supply the trend, 1h candles supply setups, and 15m candles supply confirmation. More
observations do not cause an order: a complete setup and all risk/cost checks are still required.
This is an experimental implementation, not a profitable or validated trading strategy.

## Install on the laptop

For an existing clean checkout descended from Claude `71e2140` or the quote fix `ec55bc0`:

```bash
cd ~/kestrel
git status --short
git fetch origin codex/robinhood-multitimeframe
git merge --ff-only FETCH_HEAD
uv sync --frozen
systemctl --user restart desk-rh-engine.service desk-rh-dashboard.service
uv run desk rh report
```

Keep local changes if status lists any. If fast-forward fails, preserve the checkout and reconcile its
actual commits; do not reset it. Only the two Robinhood services need restarting. Do not restart a running
Solana acceptance collector. If those services are not installed, stop the old Robinhood terminal worker
with Ctrl+C and run `uv run desk rh paper`; run `uv run desk rh dashboard` in another terminal.
The existing KESTREL Crypto window uses the same dashboard. Reload it after updating.

Report/System now show current readiness and the next 15m close plus a 30s publication grace, rather than
an 8pm daily-entry appointment. Coinbase history populates automatically; there is no import step or
mandatory 24-hour wait for this feature. Initial downloading and missing historical data can delay readiness.

## Closed-data and availability contract

`T = floor((UTC now - 30 seconds) / 900 seconds) * 900 seconds`. No current unfinished bar is used.
For each frame, its latest required end is `floor(T / frame_seconds) * frame_seconds`.
Every required bucket must exist at its exact UTC timestamp. Data must have been first received by
the decision time. Late historical data cannot appear in an earlier point-in-time query.

| Frame | Exact contiguous warm-up | Use |
|---|---|---|
| 1d | 100 closes | BTC close > SMA100; each asset's 30-day median close × base volume ≥ $1m |
| 4h | 60 complete bars | Asset close > EMA20 > EMA50 |
| 1h | 61 complete bars | ATR20, RSI2, breakouts, 24h return and exits |
| 15m | 61 complete bars | Current confirmation; at most one evaluation per asset/candle/spec bundle |

Coinbase supports 15m, 1h and daily buckets, but not 4h. Four consecutive native 1h bars are aggregated
into UTC-aligned 00–04, 04–08, etc. OHLC are first/max/min/last; volume is the sum. Partial, duplicated,
misaligned or missing components invalidate that 4h bucket. No flat candles, interpolated volume or
invented prices are generated. Coinbase is a chart/setup source, not Robinhood executable liquidity.

EMA seeds with the SMA of the first n values of the fixed warm-up window, then uses alpha = 2/(n+1).
For previous 15m EMA20, the final observation is removed from that same window. Hourly RSI2 uses the
existing Wilder implementation over 60 changes, seeded with two-change average gains/losses. ATR20 is
the arithmetic mean of 20 true ranges, using each preceding close. Return24h = last hourly close /
hourly close 24 buckets earlier − 1. These bounded definitions are recorded in immutable v2 specs.

New or thin assets, missing products, stale latest buckets and gaps abstain. The current status distinguishes
the needed bar count from the count actually present within that window. A late download can qualify
later in the same 15m interval if its candle check has not already been claimed. After sleep or an outage,
only the latest complete setup can qualify: missed historical signals are never filled retroactively.

## Experimental v2 rules

All three require the complete four-frame inputs, volume threshold, BTC regime and 4h uptrend above.
Priority is R1, R2, R3; per-strategy position limits remain 3, 3, 2. A coin cannot have two open positions
in the same strategy. Another strategy may independently hold it; the existing overall cap remains ten.

| Strategy | Additional entry | Strategy exit | Maximum holding |
|---|---|---|---|
| R1 trend | Hourly close > preceding 20 hourly highs; 15m close > previous 15m high and current EMA20 | Hourly close < preceding 10 hourly lows | 72 hours |
| R2 relative momentum | Among top 3 positive 24h returns exceeding BTC; hourly close > EMA20; previous 15m close ≤ previous EMA20 and current close > current EMA20 | Return24h ≤ max(0, BTC Return24h) | 48 hours |
| R3 pullback | Hourly RSI2 < 10; 15m close > previous 15m high and its own open | Hourly close > SMA5 | 24 hours |

R2 excludes BTC from picks, ranks only currently ready/liquid/uptrend assets and requires at least three
ready assets including BTC. This is a changing, incomplete universe, not a claim about all listed crypto.
Its ready/missing universe and input hashes are saved with every candle check. Arrival order can affect
ranks; no trade is replayed when another asset arrives later. This limitation needs forward analysis.

Shared exits: BTC regime off, asset 4h close below EMA50, or latest 15m close below the position's running
peak close minus 3 × hourly ATR20. These use known data only. Time-based exits are checked every minute,
even when an asset's candles are unavailable. A missing-data condition itself is not a price exit signal.
Quoted hard stops still run independently each quote interval (default 60s), including during downloads.
Initial stop distance = clamp(2 × hourly ATR20 / hourly close, 5%, 25%) below the estimated entry unit price.

An hourly setup can produce at most one filled entry per strategy/asset, even if the position closes and
the engine restarts before that hour changes. New data can update analysis without producing new trades.

## Execution and durable accounting

No real order methods or LIVE activation were added. Exact quantity Robinhood estimates on both routes
still govern PAPER fills and the default 2% round-trip ceiling. Entries require a fresh second estimate,
recheck STOP/halt and the candle boundary after awaiting prices, and enforce the account-relative 15%
maximum ticket against the then-current $100 + realized PAPER profit. Quotes from the future are invalid.
The round trip is recalculated using the worse entry estimate. The $100k simulated cash cushion is not
the sizing account. No confidence-based real exposure promotion is introduced by this change.

`rh_frame_checks` stores one immutable claim per asset/15m/spec-bundle BEFORE execution awaits. It retains
frame status, exact calculated features, own candle-input hash, comparison-universe input hashes, conditions,
missing assets and decision time. The source candles retain first receipt and immutable OHLCV; replay can
reconstruct the bounded inputs with `RhMarket.candles(as_of=decided_at)`. A crash after claim can miss a
PAPER opportunity; it cannot replay that candle after restart. An ENTER journal record also consumes its
hourly setup conservatively if a crash occurs before the separate ledger write. No cross-database atomic
commit is claimed. Hard-stop supervision remains independent of candle claims.

v1 specification hashes are unchanged. Existing v1 positions still use v1 daily exits; v2 positions use their
own new hashes and exit code. BTC-hold and weekly-random baselines remain v1. The old daily simulation
is still generated once per daily maintenance session, but is not attached to current v2 strategy statistics.
The dashboard/report separate v1 holdings and closed results from v2 evidence. Account totals include both.

## Laptop, API and storage budget

No new dependencies, GPU jobs, paid feeds, remote machine or AI judge. The existing read-only credentials
stay on the Dell Ryzen 7 7730U / 16 GiB Mint laptop. Only public chart data is added.

History rotates six assets per pass, with BTC first, then waits 15s. Healthy series are polled only after
their next close; unsupported products back off an hour. Startup seeds up to 240 daily, 336 hourly and 288
15m buckets per asset. At Coinbase's 300-bucket page cap this is about four requests per asset: roughly
360 for 90 assets. The existing shared limiter remains 3/s and 90/min. Download duration depends on
network/limiter/errors; a several-minute bootstrap is expected, not a fixed completion deadline.

Steady state for 90 supported assets: about 360 quarter-hour calls + 90 hourly calls per hour, plus 90 daily
calls per day (~7.6 calls/min). This excludes retries, missing products and other consumers. Robinhood's
quote/probe polling remains unchanged; qualifying entries/exits add their existing exact-size estimate calls.
No tick polling or trade is needed to gather OHLCV. Scheduler delay and outages still mean this is not a
sub-second or guaranteed-on-the-close trading system.

On an offline cloud fixture with 90 complete synthetic assets, loading all features and one conditions
pass per asset took **1.142s**, with **48.6 MiB process peak RSS** (includes the test helper imports).
This is a development measurement, not a benchmark of the laptop, the whole app, or actual network latency.
The synthetic immutable candle-check JSON averaged 11,185 bytes: approximately **92 MiB/day** for 90 × 96
checks. Candle rows, indices, quotes, decisions, WAL and backups add overhead. Reserve roughly 150–250
MiB/day for this desk initially and measure actual `~/desk-data/db` growth; this is an estimate, not a cap.
The existing 400 MiB Robinhood-engine service limit is unchanged. No retention/deletion job was added.
Existing automatic backup kinds currently cover the Solana databases, not these separate Robinhood files;
Robinhood-specific online backup/retention remains outstanding. Do not assume those files are backed up.

## Verification and work still required

Offline fixtures verify afternoon checks without daily maintenance, all three entry families, UTC close/grace
boundaries, exact 4h aggregation, availability timestamps, gaps, immutable restart checkpoints, same-hour setup
deduplication after closing, legacy exit isolation, cost rejection, STOP during a wait, and account-relative caps.
Browser coverage exercises all four intervals, readiness, version-separated cards, System timing, light/dark
and phone width. These are software tests, not forward market observations or evidence of an edge.

Still required: keyed laptop operation; provider/freshness and sleep/resume observations; CPU/RSS/storage over
time on the Dell; actual forward closed trades after costs; capacity, exposure and dependence-aware comparison
against unchanged daily v1 and matched-capital baselines. The original daily backtest cannot answer whether
this v2 is better. No promotion, profitability or LIVE readiness follows from successful implementation tests.

Long-standing gaps beyond the sync's two-bucket late-publication overlap are deliberately visible. A newly
started/restarted sync backfills from the last stored candle within its bounded 3d/14d/240d horizon; isolated
older holes that the provider continues to omit require real-data repair or remain ineligible until they roll
out of the warm-up window. No gap is silently repaired with fabricated observations.
