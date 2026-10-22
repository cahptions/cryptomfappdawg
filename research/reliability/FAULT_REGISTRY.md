# Stocks reliability campaign — 2026-10-22 UTC

Starting revision: `78cd156`, branch `codex/finish-schwab-stocks`. No operator databases, credentials, running
processes or brokerage endpoints were used. Tests use disposable databases, recorded sanitized responses,
mock transports and synthetic markets. Findings below describe software behavior, not trading performance.

| ID | Severity | Reproduction / cause | Fix | Verification / remaining limits |
| --- | --- | --- | --- | --- |
| R01 | High | A cached company's facts change on the same research date; old cache ignores source revision | Hash source facts, calculation namespace and financial classification; refresh in-memory facts; additive cache migration | Source-revision cache regression; authoritative observations and frozen decisions retained |
| R02 | High | A split executing after the panel's end rescales older prices | Apply only splits effective within the requested panel boundary | Future-split regression; later corrections/restatements still require point-in-time source archives for historical claims |
| R03 | High | Null/list/scalar JSON escapes as attribute errors; bad OHLC/negative volume/NaN enter calculations | Object and finite-number validation, consistent OHLC, named schema failures | Parser fault matrix, six JSON parsers × three wrong shapes and invalid-bar cases; rejected input creates no bars |
| R04 | High | Repeated pagination cursors or exhausted page ceiling look like complete corporate-action history | Detect repeated cursors and raise at ceiling | Repeated-cursor async fixture; job remains failed/incomplete rather than advancing its checkpoint |
| R05 | High | Deferred constraint rejects COMMIT outside rollback handler; SQLite FULL can auto-rollback before error handler runs | COMMIT inside transaction guard; rollback only an active transaction; close failed setup connections | Deferred-COMMIT and constrained SQLite page-limit tests, followed by successful writes; physical SSD/controller durability remains untested |
| R06 | Medium | Malformed Treasury/FINRA/SEC/XML/ZIP, nonfinite values and response-value echoes | Input-only schema boundaries, source-specific sanitized errors, ZIP unpack cap 128 MiB | Recorded source suite, malformed archive/date/rate and sentinel-redaction regressions; oversized archives fail visibly and need bounded ingestion before use |
| R07 | High | Missing IV becomes a fictional pre-expiry intrinsic fill; crossed/future/undated option quotes look usable | Require usable IV for models; reject invalid markets/timestamps; filter snapshots by observation cutoff; label modeled exits | Missing-IV/crossed/model flag and future-observation tests plus PAPER suite; modeled exits remain simulations, not executable liquidity evidence |
| R08 | Medium | HTTP bodies unbounded; invalid/date Retry-After; cancellation can race SQLite close | Stream decoded bodies with 64 MiB default cap, finite/date backoff, serialized close | HTTP oversize/stream closure, date/NaN retry tests, close critical-section regression and shared cancellation suite; decoder/OS resource limits are not a hard process memory sandbox |
| R09 | Medium | A surviving app worker loses Pause control after dashboard parent restarts; corrupt derived reports/settings break pages | Managed workers observe persisted Pause; restarted dashboard recognizes only managed PAPER argv; show unavailable reports/config errors; no foreign PID signaling | Managed subprocess pause/lock-release, orphan-control and corrupt-settings integration tests; phase timestamp is a checkpoint, not proof of a stalled process |
| R10 | Medium | Diagnosing a broken install requires many commands or risks dumping secrets | `desk stocks diagnose`: bounded read-only report, presence booleans, source error categories, latest receipts/phase and bounded DB checks | Missing-install, malformed-file/DB, secret-sentinel and no-bootstrap tests; a timed-out check reports incomplete rather than healthy |
| R11 | High | SIGKILL interrupts a Stocks session after debit/position writes, before COMMIT | Existing atomic ledger design verified; shared transaction guard strengthened in R05 | Exact child subprocess killed in a temporary ledger: committed $1,000 survives, position/decision absent, integrity OK, retry writable/idempotent; laptop power-loss/fsync cannot be simulated by this result |
| R12 | Medium | Incompatible fiscal years/currencies or gaps in quarterly inputs contaminate composites; overflow yields invalid ratios | Unit/period alignment, contiguous quarter checks, nonfinite output stays missing | Wrong currency/year tests plus fundamental-score suite; incomplete composites can legitimately reduce coverage |
| R13 | High | Completed days before an import outage can be lost in the explanation; observations stamped with cycle start | Preserve committed day receipts and retry remaining days; stamp prices/options at receipt completion | Partial-price outage → resume test, independently checked rows and receipt time; historical receipt archives are not retroactively created |
| R14 | Medium | Revised stock sizing and old decisions could produce a misleading current-version grade | Version the four event strategies and show mixed-specification cumulative accounts as ungraded | Ticket/risk/capacity tests and mixed-version grade regression; version-specific forward evaluation remains outstanding |
| R15 | High | Screenshot shows 100 downloaded sessions with no lists: previews require the latest unpublished close and run after optional network jobs | Publish latest available, honestly dated research before network work and after price batches; PAPER still requires its own session | Both new cases reproduced failing, then passed; 100-session preview/home populated while PAPER ledger stayed unchanged; cannot establish these were the only laptop blockers |

Main reproductions and assertions: [fault injection](../../tests/fault_injection/test_stocks_reliability.py),
[HTTP](../../tests/unit/test_http_adapters.py), [lifecycle](../../tests/unit/test_stocks_research_setup.py),
[diagnostics](../../tests/unit/test_stocks_diagnostics.py), [sizing](../../tests/unit/test_stocks_sizing.py),
[collection](../../tests/integration/test_stocks_collect.py), [dashboard](../../tests/integration/test_stocks_web.py).

## Baseline and coverage

Before fixes, Ruff and strict mypy passed. The full offline baseline was **1,061 passed, 1 failed, 5 deselected**.
The failure was the Solana SIGKILL/guardian fixture: its production-style random simulated transaction failures
could postpone the final retry beyond the test's timeout. Inspection found 14 closed and 1 EXIT_BLOCKED temporary
positions. The durability fixture now sets failure probability to zero; dedicated failure/retry tests retain their
own injected failures. No production transaction failure model was changed.

The first new reproduction group failed **22 of 28** cases before fixes. Final verification is recorded in
[HANDOFF.md](HANDOFF.md). There are no claims that every possible failure is covered.

The operator's earlier seven failures are covered by existing and retained regressions: FRED maintenance HTML,
FINRA CSV/JSON, zero revenue through research with unchanged PAPER ledger, distinct unconfigured/backfill/pending/
recorded-empty dashboard states, separate worker/dashboard locks and cancellation, CSRF renewal without automatic
retry of unrelated 403s, and selected/all-desks desktop installation. Desktop menu rendering itself needs Mint.

## Scope limits that remain explicit

- Real Schwab entitlements, revocation, refresh longevity and current provider service recovery were not tested.
- Existing stock regulatory fee constants are approximate **2025 rates**, not a freshly verified schedule.
- Daily bars do not establish intraday fills, stop ordering inside a candle, or early detection of rapid moves.
- Option snapshots are bounded and overwrite that day's expiration snapshot. Earlier intraday versions cannot be
  reconstructed. Missing usable quotes/IV remains missing; model marks/exits must not be counted as executable.
- Restated/dead listings and historical SEC availability are only as good as the retained point-in-time data.
- Physical disk failure, laptop sleep, Wi-Fi, thermals, power loss, and a long browser/collector soak remain external.
- A normal but lengthy source job may retain the same phase timestamp. It is not silently restarted as “stalled.”
- Child stdout logs still grow over long continuous operation; implement rotation separately after measuring laptop
  growth. No existing history/logs were deleted. HTTP and archive limits make large responses explicit failures.
- A corrupt authoritative DB is reported; no automatic restore, data erasure, or fabricated recovery is attempted.
