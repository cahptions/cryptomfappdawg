# M3 software and conditional sizing handoff

Later code can be developed while earlier live runs finish. M2's actual 24-hour laptop acceptance remains pending.
M3's actual six-hour SHADOW/replay acceptance remains pending. Neither requirement is replaced by an offline test.

## Delivery and laptop update

The new branch is `codex/m3-strategy-engine`, based on the delivered M2 commit `ff12741`. Let an existing laptop
collection finish before changing the files it is running. No browser file download is needed:

```bash
(
set -e
cd ~/kestrel
git fetch origin codex/m3-strategy-engine
git switch --create m3-local --no-track FETCH_HEAD
uv sync --frozen
uv run pytest
uv run ruff check src tests
uv run mypy
uv run desk doctor --offline
)
```

If switching reports existing local edits or a branch-name conflict, preserve the files and resolve that specific
issue. Do not hard-reset the checkout or erase `~/desk-data`. Existing secret files are reused.

## What is implemented

- A versioned registry of 53 numerical features with definition, unit, source, maximum age and missing policy.
- Point-in-time reconstruction using append-only observations. Future rows and mutable token/pool metadata do
  not change a historical feature. Provider claims retain their unverified status.
- Same-provider price anchors, explicit history coverage for ranges/volatility, documented original and
  age-adjusted six-hour volume-decay variants, and missing results for unrecorded quantities.
- Exact pool/quote provenance in newly recorded security assessments. Earlier dossiers remain readable but
  cannot retroactively supply missing verification or exact quote timestamps.
- A deterministic, versioned regime classifier that requires genuine trailing thirty-day context. It does not
  invent a neutral regime while history is absent.
- Complete frozen specifications for B0–B2, H1–H6, the H7 overlay and SHADOW-only R1. Scores are heuristic research
  priorities, not expected profits or trade authorization. Missing/stale security or required features block signals.
- H1 requires a verified migration occurrence timestamp. First observing a graduated pool does not establish
  when graduation occurred. Spike/retest hypotheses likewise require chronology that is not currently inferred.
- NO_TRADE always participates and wins ties. B1 uses stable seeded priorities; B2 uses observed momentum.
- SQL-enforced immutable strategy versions, append-only feature snapshots/SHADOW decisions/security dossiers,
  complete input/configuration hashes, and decision-linked raw-evidence retention.
- A bounded round-robin SHADOW runner over existing collector records. It makes no provider requests and writes
  no positions. Replay streams persisted inputs, validates original integrity even during copied experiments,
  and stores original/alternative specifications in the separate research registry. Hash changes and chosen-action
  changes are reported separately.

## Conditional sizing refinement

`desk.risk.sizing` is a standalone tested foundation for M5/M6, with no order submission or runtime integration.
Baseline sizing capital is the lesser of initial capital and current equity. Only positive closed/reconciled net
profit, backed by fresh evidence for the exact PAPER strategy version and regime, unlocks a larger capital base.
The percentage risk fraction stays fixed. The initial evidence policy uses the existing complete confidence
matrix, 300 trades, ESS 150 and 60 days; deposits, open gains and winning streaks do not count.

Every larger target needs a fresh quote for its exact ticket and complete fixed-fee evidence. Free capital,
reserve, pool liquidity, exposure, drawdown and all configured ceilings still cap or reject it. The current
account-relative ceiling is 15% of eligible capital, following the operator's refinement in this session.
An optional absolute SOL limit can additionally tighten an older/custom profile. Missing or deteriorating evidence reduces scale or
prevents authorization. This is neither a confidence-matrix producer nor a completed PAPER engine.

## Run SHADOW and inspect actual evidence

After updating, keep `desk discover` running in one terminal to provide fresh observations. In another:

```bash
cd ~/kestrel
systemd-inhibit --what=idle:sleep --mode=block --who=KESTREL --why="SHADOW acceptance" \
  uv run desk shadow --minutes 360
uv run desk shadow-report
uv run desk replay --require-hours 6
uv run desk db check
```

If no collector supplies fresh observations, decisions remain stale/missing and NO_TRADE; the SHADOW runner
does not fetch replacements. Current global-context collection/reference import is not wired to this runner,
so measured regime history is insufficient by default. There is no shortcut that treats missing thirty-day
history as validated. Exact migration and event chronology also remain unavailable unless separately captured.

A copied threshold experiment accepts `desk replay --specs-file specifications.json`, where the file is an array
of complete strategy specifications. It cannot satisfy unchanged-replay acceptance. No source evidence changes.

## Keep an already-running discovery test awake

In a second terminal, while the discovery process is already running:

```bash
systemd-inhibit --what=idle:sleep --mode=block \
  --who=KESTREL --why="Keep awake until discovery finishes" \
  bash -c 'while pgrep -u "$UID" -f "[d]esk discover" >/dev/null; do sleep 15; done'
```

Keep that terminal open and the laptop plugged in. The inhibitor exits once discovery finishes. Display
blanking is allowed; suspending is blocked. If discovery is not running, the monitor exits immediately.

## Outstanding milestones

Cloud checks: **376 offline tests passed**, four live tests deselected, Ruff clean and strict mypy clean across
73 source files. Frozen dependency installation and wheel build passed. Actual short CLI smoke recorded 20
NO_TRADE decisions at 37.1 MB peak RSS; all 20 replayed identically, and the six-hour gate refused the short run.
All four database integrity/WAL checks passed. These are cloud measurements; exact smoke outputs are retained in
`research/evidence/2026-10-19/m3_cloud_smoke.json`.

M2 needs actual 24-hour operator evidence. M3 needs actual six-hour SHADOW/replay evidence and measured context
integration before regime-dependent research can produce eligible signals on collected data. M5 still needs the
full PAPER wallet, guardian, execution/cost model and 72-hour run; M6 needs real statistics/matrix production;
M7 needs the full dashboard; M8 needs zero-funds infrastructure tests; M9 needs actual calendar-time evidence.
Work on those implementations need not wait for earlier timers. Acceptance, performance claims and real-money
authorization stay separate. M4 remains dropped; no paid or LLM dependencies were added.
