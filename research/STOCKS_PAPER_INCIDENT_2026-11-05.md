# Stocks PAPER incident and requirements review — November 5, 2026

## Scope and evidence

Reviewed `origin/claude/funny-rubin-gq99vj` at `30052f003e52ca98a47acd0ab6ca9794bea01525`, fetched in the existing clean
`/workspace/kestrel-schwab` checkout. The previous local remote reference was `972565b`; relying on it would have
missed substantial Stocks changes. No AGENTS.md was found in this checkout or its workspace parents. Read the
uploaded four handoffs, current PROGRESS, ACCEPTANCE, continuation and October 29/30 handoffs, Stocks Plays,
Schwab and PAPER docs, browser instructions and the relevant source/tests. No other desk, production database,
credential, real order, LIVE authorization or saved environment repository selection was changed.

Both old-chat report attachments were recovered. They contain the same health snapshot taken at
`2026-11-05T18:43:10.737988+00:00`, version `30052f0`: worker running, progress in options, 507 stored sessions,
2,446,621 price rows, latest daily session November 4. There is no crash traceback in either attachment.
The market database check says `unavailable_or_check_incomplete`, with source health empty; that is not evidence
of database corruption. Option coverage exclusions likewise do not establish an exit cause.

**The operator's exit-code-1 incident remains open. Its actual exception has not been obtained or reproduced.**
An exact error/log excerpt was requested while independent work continued. No invented root cause, blanket
retry, database reset or suppression of worker failures was introduced.

## Fault registry

| ID | Priority | Reproduction / cause | Change and verification | Remaining limitation |
|---|---|---|---|---|
| PAPER-01 | High | Operator reports PAPER stops with code 1; uploaded snapshot precedes the failure and has no traceback | Traced GUI → ResearchWorker → `python -m desk stocks paper --continuous --managed` → runner cycle; added retained crash context at the CLI boundary | Actual operator exception still required; no claim of a crash fix |
| PAPER-02 | High | Any child crash becomes only a generic code and a filesystem log instruction; old diagnostic has a short exception-type allowlist and no source location | Retain a private bounded failure record with time, PID, allowlisted exception type and up to eight desk-relative file/line locations; supervisor matches child PID; Evidence renders and downloads it | No exception messages, source text, locals or request values are exported; SIGKILL/OOM/interpreter failure before entering CLI cannot write the record |
| HEALTH-01 | Medium | A large DB can exceed the 0.5-second quick_check deadline; one surrounding try block then skips history and source summaries | Distinguish SQLITE_INTERRUPT as an incomplete integrity check and give inexpensive summaries their own bounded deadline | Does not certify integrity when the budget expires; original report alone cannot establish why its check failed |
| LEARNING-01 | High product mismatch | `learning.initialize` records `research_cap`; `learning.fund` refuses automatic virtual top-ups (`f68d5ee` says this followed a later user request). Uploaded requirement explicitly asks for uncapped research | Documented the conflict; finite and research books untouched during the incident work | Requires reconciling the research-funding policy separately; current implementation does not satisfy the uploaded uncapped requirement |
| TRACKING-01 | Medium product gap | `play_tracking.tracked_set` excludes `@` expiry-band cohorts; Plays docs say those cohorts receive expiry grades, not intraday/daily tracking | Verified distinction in code and docs | Does not satisfy a literal requirement to track every generated band's full P/L path automatically |

The new failure record is `stocks-paper-failure.json` in DeskPaths.home, written atomically with mode 0600. It
retains the latest failure across retries/restarts; Evidence labels it as historical so a running process is not
described as currently failed. Malformed/oversized records are rejected. The supervisor does not automatically
restart failed children. Pause and STOP behavior, book funding and strategy rules are unchanged.

## Fault-injection and acceptance matrix

| Boundary | Injected / observed case | Expected result and verification |
|---|---|---|
| CLI failure | AttributeError containing a private sentinel during the awaited cycle | Original exception propagates; lock released; private failure record retains source frames, never sentinel or absolute path |
| Diagnostic storage | Failure-record write raises OSError | Original KeyError still propagates; fixed notice reports diagnostic write failure |
| Actual child startup | Invalid TOML in disposable config, real Popen/CLI | Child exits 2, ConfigError and core/config.py frame retained, no databases created; dashboard recreation preserves record and explicit Pause |
| Process supervision | Child code 1 plus matching retained NameError | GUI state names failure; three maintain calls spawn only once; a different PID is not assigned the old crash |
| Untrusted record | Wrong exception type, array type, boolean PID, invalid frame/path/line/time | Record omitted from exports |
| Database check | SQLITE_INTERRUPT during quick_check on disposable databases | Integrity explicitly not checked; source rate-limit health, history and PAPER-mode verification still available; ledger unchanged |
| Browser | Synthetic failure and timed-out integrity response | Type, relative source location, historical-failure label, bounded-check explanation and downloaded JSON present at 1366px and 375px; no horizontal overflow or page errors |
| Existing lifecycle | Existing research-setup tests | Pause persists; managed orphan can observe Pause without foreign PID signalling; continuous PAPER cancels network waits and releases lock; no crash restart loop |
| Broader GUI | Existing ui-workflows browser | Charts/calculator, health, owned evaluator start/cancel/complete and preservation of PAPER positions/events |

These are software and synthetic-fixture checks, not authenticated Schwab verification or confirmation that the
operator's failure is cured. Fresh final counts and commands are recorded in PROGRESS.md.

## Requirements coverage at the inspected revision

This is a source/test review, not a new human usability study or comprehensive financial-model certification.

| Uploaded requirement | Current evidence / assessment |
|---|---|
| Collection and recovery | runner separates research-only preview and PAPER processing; optional source handling and persistent jobs exist. Fatal analysis/programming errors still stop the process deliberately. New crash diagnostics improve recovery visibility. Actual laptop failure unresolved. |
| Stock profiles/charts/ETFs | stock_page, stock_views and shared charts implement sourced profiles, ETF treatment, listed ranges, responsive inspection and gaps. Existing GUI workflow exercises long histories and calculator; live source population unverified here. |
| Discovery lists | lists/research UI implement market-only Trending, separate WSB and ranked research explanations. Current source and regression suite present; this review does not assert every live list has eligible names. |
| Options discovery | options_view, activity and Plays distinguish market, attention and learning evidence; quotes are snapshots. Paid entitlements and laptop feed freshness remain external. |
| Affordable contracts | Plays searches budget alternatives; default All prices and explicit reset documented/tested. No permanent $475/$500 ceiling added. |
| Play details/calculator | Dedicated play dialogs and multi-leg modeled scenarios exist; mixed-expiry valuation was changed by November 5 work. Broad GUI test exercises controls, not independent validation of every financial formula. |
| Probabilities | odds, plan_odds and play_grades distinguish ITM, profit, target/touch and plan outcomes; calibrated cohorts are data-dependent. No claim of predictive validation from tests. |
| Retention/personal tracking | play_tracking, play_history, play_selections and personal tracking retain ideas/quotes and confirmed PAPER actions. Expiry-band cohorts have the TRACKING-01 limitation. |
| Before-expiry P/L | Quote tracking and saved mark paths exist; repaired/modelled history is distinguished. Missing unobserved peaks cannot be recovered as real quotes. |
| PAPER/replay | paper engine/report, replay archive and backtest worker separate chronological replay from forward records, with recovery sessions disclosed. Existing restart/STOP and replay tests retained. Operator startup is not yet accepted. |
| Uncapped Learning | Starting $500,000 and retained funding/P&L exist, but current cap and no-top-up behavior conflict with the uploaded override (LEARNING-01). No policy changed in this incident patch. |
| Finite reference books | learning_retail makes account-specific whole-contract/cash decisions for $1k/$5k; positive prior research evidence selects a family, with a labeled starter otherwise. Best-checkpoint selection has survivorship limitations stated in code; it is not independent forward proof of an edge. |
| Evidence/recovery UI | Health route and owned evaluation controls exist. New retained failures now reach Evidence and its download; current GUI checks cover those paths. |
| Other desks/environment | Arbitrage, Crypto, Solana, environment repository selection and publication settings untouched. No need to repair the old publishing job before repository work. |

## Obtain the change and collect the missing evidence

On the computer actually running Stocks (the repo may also have a remote launcher), preserve local changes and
stop if ff-only reports divergence:

```bash
cd ~/kestrel
git status --short
git pull --ff-only origin claude/funny-rubin-gq99vj
uv sync --frozen
uv run desk stocks restart
```

Use the existing current restart command rather than a browser refresh: static assets are captured at server
startup. Then use **Start PAPER** in the app. If it stops, open **Evidence → Check data health → Download health
report** and share that one file. It now includes retained type/source context; it never exports raw messages or
credentials. If more evidence is necessary, request only the implicated portion, not repeated whole-report pastes.
The optional bounded terminal equivalent remains `uv run desk stocks diagnose`.

Immediate acceptance still requires identifying the actual operator exit cause, reproducing and correcting that
cause, and confirming startup, continued operation, Pause/STOP and restart on the affected installation. This
patch completes diagnostic repairs and a prioritized source audit, not that final acceptance.
