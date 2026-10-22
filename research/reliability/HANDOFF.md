# Reliability handoff — 2026-10-22 UTC

The Stocks/options audit and requested Stocks sizing reassessment are implemented on `codex/finish-schwab-stocks`.
The [fault registry](FAULT_REGISTRY.md) records reproductions, corrections, tests and external limits.
This is a software verification result. Timed forward evidence and profitability remain unestablished.

## Verified results

| Check | Actual result |
| --- | --- |
| Baseline full offline pytest | 1,061 passed, 1 failed, 5 deselected, 152.46 s |
| Final full offline pytest | **1,123 passed, 5 deselected**, 108.92 s |
| Ruff | All checks passed |
| Strict mypy | No issues in 208 source files |
| Production UI | `npm run build` in `webui`: TypeScript and Vite passed |
| Stocks populated browser | 1366×900 and 375×900: navigation, candles, watchlist, PAPER, private Settings, stale-session renewal, OAuth cancellation, unrelated 403 handling and CSP passed |
| Stocks empty browser | Same widths/workflows passed; no invented market prices |
| Readiness browser matrix | Five states at both widths: unconfigured, partial history running/stopped, first lists pending, genuinely recorded empty; no horizontal overflow |
| Published-history browser state | Both widths: explicit older research date and missing-newer-close explanation; pending cards do not falsely claim zero qualifiers |
| Synthetic resources | 500 companies, 503 sessions, 252,003 bars; research preview 11.56 s wall / 11.54 s CPU; whole-process peak RSS 217.7 MiB; DB/report/WAL files 97.2 MiB |

One dependency deprecation warning remains: Starlette's test client warns about its `httpx` integration. It is
not a failed check; no unrelated dependency upgrade was introduced. The five deselected tests are the configured
online tests. No real provider availability, trade outcome or laptop runtime is inferred from these results.
The baseline guardian test failure was isolated to random simulated failures inside the durability fixture,
as recorded in the registry; separate failure/retry tests continue to exercise failures.

Reproduce offline checks from a checkout with locked development dependencies:

```bash
uv sync --frozen
uv run pytest -q --tb=short
uv run ruff check .
uv run mypy
cd webui
npm ci --ignore-scripts --no-audit --no-fund
npm run build
```

The managed sandbox interrupted async/thread wakeups during fixture tests; pytest/browser checks were run with
approved execution outside that restriction. All financial state still lived in disposable test directories.
Browser recipes are in `tests/browser/README.md`. Stocks uses `tests.browser.stocks_fixture_server`,
`stocks_smoke.mjs` and `stocks_readiness.mjs`; install Playwright outside the checkout and copy the scripts beside
that installation so their ESM import resolves. The fixture's `auto_start=False` prevents actual source workers.
Resource measurement is reproducible with `uv run python -m research.reliability.benchmark`; see
[the measured artifact](RESOURCE_CHECK.json). Synthetic benchmarks intentionally use test-market utilities.

## Material behavior changes

- Bad source inputs become explicit sanitized source failures; correct inputs from other sources can continue.
  Response bodies default to a 64 MiB decoded cap and SEC archives a 128 MiB unpack cap. Oversize is an error,
  never reported as a completed history download. These limits can reject unusually large legitimate archives.
- Fundamentals cache revision includes source facts and calculation/classification inputs. Research method v2
  separates changed calculations from old method hashes. Stricter missing/unit/period semantics can reduce coverage.
- New receipts use response-completion time. A partial price pass retains completed days and retries the rest.
- Screenshot follow-up reproduced two additional blockers: research waited for an unpublished latest close and
  only ran after optional network work. Research now appears from latest published history at cycle start and
  after each price batch, clearly dated. A synthetic exactly100-session case populates previews/home without
  stepping PAPER. No claim is made that this identifies every blocker on the actual laptop.
- SQLite COMMIT failures and automatic rollback are handled; partial initialization closes opened connections.
- Option data arriving after a decision is unavailable to it. Invalid markets/timestamps are filtered, usable IV
  is required before expiry, and simulated modeled exits are labeled. Executability remains unproven.
- System displays a work phase and its last checkpoint time, corrupt derived reports and bad connection preferences.
  A surviving **new managed** PAPER worker honors a persisted Pause even after its dashboard parent restarts.
  The replacement dashboard never signals arbitrary PIDs from a saved file.
- `uv run desk stocks diagnose` provides one bounded, local, read-only report: version, configuration presence,
  worker locks/automation, work phase, source successes/failure categories, recorded history and DB checks.
  Missing installs create no files; unavailable/corrupt/busy DB checks report honestly. No secrets/provider calls.

## Stocks-only sizing experiment

Stocks commissions were already modeled at zero. Crypto percentage fees never governed Stocks tickets.
The following are PAPER experiment defaults, not an evidence-based recommendation to increase real exposure:

| Event strategy | Old desired allocation | New desired allocation | Effective default at $10,000 equity |
| --- | --- | --- | --- |
| Earnings drift | 10% | 15% | Up to $1,500 |
| Breakout | 10% | 15% | Up to $1,500 |
| Insider cluster | 10% | 15% | Up to $1,000: 20% stop × ticket is limited by planned risk |
| Squeeze runner | 5% | 7.5% | Up to $750 |

For each event stock ticket, the model takes the minimum of desired allocation, **20% of equity**, **2% planned
account risk divided by stop distance**, available cash buffer, and **0.5% of observed daily dollar volume**.
It checks risk/cash/capacity again at fill. A stop can gap and lose more than its planned amount; correlation
between positions is not captured by that per-ticket budget. There is no leverage or automatic profit-only scaling.
Lower commissions reduce friction; they do not prove a larger or more frequent trade has positive expectancy.

Stock slippage and regulatory costs remain. Fee constants are approximate 2025 values and were **not** independently
verified against a current fee schedule. Option contract/spread costs and limits remain separate. Crypto/Solana/RH
capital, fee models and strategy settings remain unchanged; shared HTTP/storage fixes passed the full suite.
Stocks still uses separate $10,000 stock and $50,000 option strategy books; the $500 crypto reference is unrelated.

Event versions are now v2. Prior positions/history are not erased or relabeled. Cumulative accounts containing older
specifications cannot grade the current version; separate version-specific evaluation remains necessary.
The daily-close engine's cadence and signal thresholds did not change. More intraday opportunities require actual
intraday data, availability/execution rules and forward evaluation, not just removal of a fee threshold.

## Operator update and observable acceptance

For a dashboard started in a terminal, press **Ctrl+C in the Stocks dashboard terminal once** to deliberately
restart the backend for this update. This is different from saving credentials; normal credential setup keeps the
dashboard running. Then:

```bash
cd ~/kestrel
git pull --ff-only origin codex/finish-schwab-stocks
uv sync --frozen
uv run desk stocks dashboard --open
```

The newer startup recovery also supports `uv run desk stocks dashboard --restart --open`, which safely identifies
and gracefully stops this data directory's current-user Stocks dashboard before reopening it. Ordinary repeated
`--open` reuses a responding server. This avoids the generic worker-busy error when a desktop background server
is still alive. The restart command does not select arbitrary collectors or other desks, delete locks, or force kill.

If using a background desktop launch, closing the browser window does **not** restart its backend. Stop just that
Stocks backend with the following command, then use the dashboard command above after it exits:

```bash
pkill -TERM -f '[d]esk stocks dashboard'
```

Do not use global `desk down` to update Stocks: that affects other desks. Do not delete lock files, history or
credentials. An existing external collector is observed rather than duplicated; the combined PAPER worker starts
after its lock releases. Automatic PAPER remains enabled by default. An explicitly saved Pause stays paused;
in that case click **Start PAPER** once in System. STOP remains an entry veto and must never be cleared implicitly.

Observe:

1. System shows a running combined PAPER/collection worker, or a clear intentional pause/external-worker state.
2. Work phase/checkpoint and downloaded receipts advance as completed work arrives. A phase can last minutes.
3. Source errors name missing/unavailable inputs; unrelated downloaded research remains accessible.
4. Backfill creates progressive research previews. PAPER decisions wait for actual session/history prerequisites;
   no 24-hour timer is required and an old session does not fabricate a contemporaneous fill.
5. PAPER shows the separate account balances, current strategy specs and any mixed-version warning. Actual order
   count may remain zero if no valid signal/next-session execution exists. `orders_enabled` remains false.

If these observations fail, collect one report without stopping anything:

```bash
cd ~/kestrel
uv run desk stocks diagnose
```

## Remaining external evidence and prioritized continuation

1. Use that single diagnostic only if the laptop still fails; the cloud cannot inspect its live state.
2. Observe real Schwab refresh/revocation and option data coverage without enabling orders. Existing authorization
   success reported by the operator is not evidence of long-term refresh or all-contract availability.
3. Measure Mint RAM/CPU/thermals and log/storage growth during a bounded soak. The 500-company cloud fixture is not
   a full-universe laptop guarantee; keep one Stocks worker and bounded API batches. No GPU/paid API is required.
4. Add measured log rotation and, if needed, streaming archive ingestion; do not silently raise limits indefinitely.
5. Collect distinct forward observations for v2 sizing. Do not use synthetic successes, mixed-version grades,
   model-based option exits or sleep-gap replay as evidence of real profitability.
6. Verify current regulatory/broker costs before relying on fee precision. No new remote/API budget was introduced.

LIVE authorization, real brokerage orders, operator accounts and wallet funding were not changed.
