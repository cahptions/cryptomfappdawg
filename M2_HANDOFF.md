# KESTREL — M1 closure and M2 implementation handoff

M1 is complete using the operator's supplied laptop evidence. M2 software is implemented and tested; its full
24-hour discovery acceptance is **pending**. M3 and subsequent milestones are not claimed complete.

## Fetch the M2 branch into the existing laptop checkout

The implementation is based on commit `df4866d0ce4c6029236cd02f6541fbf9ff2965f5` from the existing Claude branch.
The delivery branch is `codex/m2-discovery` in the same repository. Fetch it directly from the laptop; no browser
download or file in `~/Downloads` is required. The commands stop if any step fails:

```bash
(
set -e
cd ~/kestrel
git status --short
git fetch origin codex/m2-discovery
git switch --create m2-discovery --no-track FETCH_HEAD
uv sync --frozen
uv run pytest
uv run ruff check src tests
uv run mypy
uv run desk doctor
)
```

If switching branches reports conflicts or an existing branch name, preserve the current files and resolve the
specific issue; do not reset local changes or delete `~/desk-data`. The changes add migrations; applied migrations
0001/0002 remain unchanged. The existing private secrets file is reused and is never committed to the repository.

## What M2 adds

- Resumable discovery from the persisted M1 dataset and ongoing provider collection.
- One candidate per canonical mint/pool, with separate source observations and independent creation times.
- Six discovery reasons, including evidence-based migration and activity changes. Ordinary refresh does not
  manufacture renewed activity. Promotional/profile discovery is labelled `PROMOTED`; remote metadata is not fetched.
- Cheap universe/activity filters before bounded on-chain work. Trade counts are not distinct-wallet counts.
- Fail-closed mint/freeze authority checks and Token-2022 extension allow-list.
- Pool program/PDA checks using `solders`, official pinned Pump documentation, decoded vault references and token
  authorities, current LP burn checks, and on-chain liquidity/price comparisons.
- Top-20-account concentration aggregated by on-chain owner, with raw and adjusted metrics. Only verified
  infrastructure/burn accounts are excluded. The supply denominator is unchanged; observations remain lower bounds.
- Explicit Jupiter Shield warning policy and exact-quantity fresh buy/sell quote checks at the configured ticket.
- Append-only stage evidence with values, thresholds and configuration hash; atomic candidate transitions;
  security PASS requires a matching assessment. Early-stage passes remain pending.
- Fair bounded assessment queue, early rejection before spending further requests, shared cross-process request
  counters and configured sliding-window limits in the run report.
- Loopback-only read-only console, strict CSP and text rendering, no trading controls or LIVE arming route.
- `desk discovery-report --require-hours 24` to distinguish completed long-run evidence from smoke tests.

## Validation performed in the cloud

- Frozen dependency installation; **212 offline tests**, Ruff and strict mypy; wheel build including the SQL migrations.
- Existing four live read-only provider tests rerun successfully.
- Actual read-only public RPC PumpSwap account decoding, with raw evidence saved. Unsupported observed features
  remain explicitly rejected. The authoritative account layout and documentation are pinned to Pump docs commit
  `639b87fb45c3adeffa6f7c4a1bf0cb85e392bf22`.
- Actual keyless Shield request; explicit mint and two informational warnings returned; raw response retained.
- Two short two-minute discovery runs plus a final one-minute smoke run; all had zero unhandled exceptions and
  zero ingest errors. The latter two had peak collector memory **57.3 MB** and **57.6 MB**, respectively.
- Final smoke report: **573 cumulative candidates**, **0 orphaned candidates**. Cumulative counts include previous
  smoke runs; `during_run` separately reports event counts for the selected collection window.
- CLI console started, its status endpoint returned functional SHADOW data, and the owned process was stopped.
- Database integrity and WAL checks passed for all four files.
- The one-minute run was checked against the 24-hour gate and correctly **failed duration acceptance**.
- Run duration now records a monotonic measurement and successful completion of the requested timer, so a small
  NTP clock adjustment cannot falsely shorten a completed run and an early stop cannot claim timer completion.

Evidence: `research/evidence/2026-10-19/m2_cloud_smoke_runs.json` and the adjacent raw protocol/Shield responses.
These are cloud measurements, **not measurements of the operator's Ryzen laptop**. Laptop M1 peak memory was
50.9 MB as reported by the operator. No GPU is used by M2; the only added runtime dependency is pinned `solders`
(and its small transitive dependency) for vetted public-key/PDA handling.

## Remaining M2 acceptance on the laptop

Plug in the laptop and prevent sleep for the collection session using the operator's normal power controls.
The existing keyed doctor result was already 19 OK / 1 swap warning / 0 failures; do not create duplicate keys.

```bash
cd ~/kestrel
uv run desk discover --minutes 1440
uv run desk discovery-report --require-hours 24
uv run desk db check
```

Keep the completed summary and acceptance JSON. Verify discoveries by source/reason, stage outcomes, rejection
reasons, provider allocations/limits, peak memory, database growth and orphan count. Do not count an interrupted,
short, or zero-work run as M2 acceptance. The cloud has no configured keyed RPC; the laptop's existing free
keyed RPC is the intended source for sustained holder resolution.

## Explicit coverage limits

- Pool verification currently supports canonical PumpSwap migration pools; other venues remain unverified and
  cannot pass. Current LP supply must be zero; canonical identity alone is insufficient.
- Mayhem, cashback, holder rewards, virtual quote reserves, editable creator fees and unknown nonzero account
  tails remain unsupported and rejected until separately verified. They are not silently treated as safe.
- Holder coverage is the top twenty token accounts, not a complete beneficial-owner census. Dev holdings absent
  from the observed set remain unknown. Suspected clusters cannot claim proven common ownership.
- Unknown fixed transaction fees are explicitly labelled; the M2 round-trip number is quote-implied plus reported
  fixed fees, not proof of a fill or a complete M5 execution-cost model.
- Provider counters measure request allocations, not Helius monthly credits or billing. The free tier must remain
  selected; there is no paid fallback. Historical observations and audit data still require measured retention
  planning as the dataset grows.
- The status console is an early operational view. Full React panels, charts, controls and operations are M7.

## Next milestone

After recording M2's actual acceptance results, continue M3: point-in-time feature registry, interpretable regimes,
versioned strategies, deterministic ranking with NO-TRADE, SHADOW decisions and replay. Then M5 PAPER, M6
statistics, M7 full dashboard, M8 zero-funds infrastructure and M9 forward evidence. M4 remains dropped. M9's
calendar-time evidence cannot be replaced by simulated history.
