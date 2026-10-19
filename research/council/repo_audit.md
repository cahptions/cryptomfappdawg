# KESTREL supplemental council: repository audit

Research/design audit, 2026-10-19. Branch inspected: `codex/continue-claude`. This file is the only change made by this audit. No provider requests, collector starts, secrets reads, trading changes, or elapsed-time acceptance claims were made. No applicable `AGENTS.md` was found at `/`, `/workspace`, or the repository root. Required project documents and relevant source/tests were inspected; document claims below are distinguished from source behavior.

## Conclusion

The existing hypotheses already cover ordinary momentum, breakouts, pullbacks and renewed activity. A council should measure **common-factor-residual continuation** and **temporary-liquidity-shock reversal**, with the latter explicitly experimental. Their mechanisms differ, but their independence is unproven and must be measured against H2/H4/H7 in particular. Both initially have zero influence.

Neither mechanism is ready for an honest executable historical backtest from current tables alone. A residual advisor needs synchronized closed token/SOL bars with reception times. A shock-reversal advisor additionally needs a genuinely observed quote/liquidity deterioration-and-recovery sequence; a chart dip alone is insufficient. Store prospective evidence before considering trade influence.

## Existing strategy audit

Source of exact definitions: `src/desk/strategies/specs.py:115` and `src/desk/strategies/evaluate.py:147`. Defaults are hypotheses, not validated findings.

| ID | Actual specification | Overlap relevant to council |
|---|---|---|
| B0 | Cash/NO_TRADE; zero score and no orders | Required baseline |
| B1 | Stable seeded priority among eligible candidates | Selection baseline; not an independently observed random counterfactual portfolio |
| B2 | Score = 100 × 1h same-source log return | Unadjusted momentum baseline for residual continuation |
| H1 | MIGRATION occurrence 600–3,600 s ago, distinct buyers ≥100, claimed organic share ≥0.5, 15m log return ≥0 | Broad continuation overlap; see blockers below |
| H2 | Initial spike ≥30%, retracement 20–60%, volume acceleration 0.8–1.25, 1h liquidity change ≥0 | A shock reversal must demonstrate transient liquidity evidence beyond a post-launch pullback |
| H3 | Within 0.5% of six-hour observed high, 1h log return ≥0.10, liquidity change ≥10%, buyers ≥100; max4h | Breakout overlap; residual signal must show incremental effect controlling raw momentum |
| H4 | VOLUME_EXPANSION discovery, volume acceleration ≥2, drawdown from six-hour high 10–30%, liquidity change ≥0, 5m return ≥0 | Largest price-pattern overlap for recovery advisor |
| H5 | One-hour range ≤10%, chronology-based breakout/retest confirmed, 15m return ≥0, liquidity change ≥0; max8h | Retest overlap; chronology currently unavailable |
| H6 | RENEWED_ACTIVITY, creation age >24h, acceleration ≥1.5, 1h return >0, buyers ≥100; max24h | Older-token continuation overlap |
| H7 | Overlay requires both original and age-adjusted volume-decay inputs; rejects adjusted decay <0.5 or 5m return <−0.10 | Must stay visible in all ablations; never count it as a third council strategy |
| R1 | Short acceleration ≥1.5, positive5m return, buyers ≥100; max1h; SHADOW only | Do not reintroduce fast momentum under another name |

Tradeable specs B1/B2/H1–H6 are v2 SHADOW/PAPER children; H7/B0 remain v1 (`specs.py:210`). Common requirements include verified liquidity, exact-ticket round-trip quote cost ≤3%, quote age≤10s, security PASS and independent guardian. Default stop10%, trailing activation15%/drawdown10%; these are inherited controls, not evidence of an edge (`specs.py:81`). Ordinary H1/H2/H4/B1/B2 default maxhold6h.

### Material current-data limitations

1. **H7 currently vetoes every primary entry under the normal feature-building path.** `features/builder.py:196` computes `volume_decay_adj` as `UNVERIFIED` because its token-creation age is a provider claim; otherwise the feature remains missing. `strategies/evaluate.py:80` rejects required non-OK features. Every H7 reason is propagated into all trade hypotheses at `evaluate.py:166`–186. Consequently the ordinary builder supplies either an unverified or missing H7 prerequisite, independently of advisor signals. This is a deliberately fail-closed consequence of present provenance rules and an operational blocker, not permission to weaken them. Synthetic unit contexts can supply OK values.
2. H1 also requires `migration_occurred_at`, which defaults to None (`contracts/strategy.py:188`), while `ops/shadow.py:66`–69 does not populate it. Its organic share is also deliberately UNVERIFIED (`features/builder.py:313`). H2's initial-spike and retracement chronology and H5's retest chronology remain explicit missing features (`features/registry.py:93`). H6's creation-age requirement remains UNVERIFIED. These gaps matter when defining the unchanged-primary baseline.
3. Source tests explicitly preserve the above truth distinctions: `tests/unit/test_features.py:125`, `:136`, `:163`; required non-OK claims fail at `tests/unit/test_strategies.py:151`. Migration first-seen is not verified occurrence (`tests/unit/test_strategies.py:190`).

**Offline verification:** a disposable SQLite database under `/tmp` was migrated, seeded with the existing `tests/unit/test_features.py` snapshot helpers, then evaluated through the actual `FeatureBuilder`, `builtin_specs`, and `evaluate_decision`. No creation claim produced status `missing`, NO_TRADE, and `OVERLAY_H7:FEATURE_MISSING:volume_decay_adj` in every tradeable row. A24hcreation claim produced numeric value1, status `unverified`, NO_TRADE, and `OVERLAY_H7:FEATURE_UNVERIFIED:volume_decay_adj` in every tradeable row. That fixture also lacked fresh verified liquidity/quotes; those independent blockers do not remove the H7 veto. This was a narrow behavioral check, not a full-suite rerun or a check of laptop records.

**Priority handoff:** implementation of PAPER decisions is not evidence that the production pipeline has actually opened trades or learned from outcomes. Confirm actual ledger/event counts on the laptop before describing any primary-trading sample. Separately design a provenance-respecting policy decision for H7/chronology/claimed-feature limitations. Register any resulting engine-policy change as a new version and preserve its original NO_TRADE baseline; this council research does not implement that change.

## What data actually exists

| Evidence | Available shape and limitation |
|---|---|
| Market snapshots | Append-only-use rows per pool/provider, receipt `observed_at` plus exact decimals/statuses, rolling volume/count windows and raw digest (`storage/migrations/market/0001_init.sql:25`). Latest mutable token/pool rows are display caches, not historical truth. |
| Feature series | Same pool and source only; historical query bounded6h+120s; return anchor≤120s away; continuous-series calculations reject gaps>180s (`features/builder.py:107`, `:221`). Volatility is stddev of adjacent irregular observed returns, explicitly cadence-dependent (`features/registry.py:49`); do not substitute it for fixed5m realized volatility. |
| Token OHLCV | Actual provider5m bars only, identity-checked, first accepted observation immutable, raw digest, receipt time. Collector asks200bars per pool, at most2pools/min, held positions first then up to20recent PASS/DEFER candidates (`ops/candles.py:25`, `:65`; migration0007). Initial batch≈16h40m, not a long-term sample; repeated collection can accumulate longer history. |
| Chart response | Default200points, hard cap500, additional100warm-up rows; gaps restart EMA/RSI; no provider fetch on GET (`web/charts.py:96`). These indicators are descriptive and are not strategy features. |
| SOL benchmark | Collector fetches70one-minute SOL/USDC bars each minute, computes overlapping1h return/volatility, stores aggregate context and raw digest (`ops/collector.py:212`, `regime/context.py:25`). There is **no persisted normalized synchronized SOL close table**. The aggregate1h return is not a substitute for5m benchmark returns. Raw packets may allow reconstruction with original reception times, subject to retention and validation. |
| Bar time semantics | `contracts/market.py:64` still says provider timestamp is believed to be start/TO_VERIFY; `RESEARCH.md:422` likewise. Token ingestion assumes start and rejects bars with start+5m after receipt/current time (`ops/candles.py:93`). Resolve semantics before advisor promotion; retain both event interval and received time. |
| Quotes | Exact input/output mint and raw amounts, receipt time, unavailable outcomes, payload/raw digest (`migration0001:58`). Security round trips are linked exact-ticket pairs, not a dense quote panel (`features/builder.py:377`). Existing quote frequency cannot be assumed adequate for shock/recovery detection. `quote_impact_slope`, `quote_variability` and `exit_capacity_sol` are registered at `features/registry.py:105` but have no builder implementation; their names are not available measurements. |
| Security/holders | Verified pool-reserve/liquidity evidence and top20-account owner lower-bound concentration when available (`features/builder.py:354`). Full beneficial ownership and organic-volume verification are unavailable. Jupiter organic/holder/developer metrics remain claims, not ground truth (`features/registry.py:75`). |
| Regime | Stored context includes SOL return/volatility, watchlist volume, migration count and liquidity-loss hazard. A calibrated reference requires30actual measured days (`regime/context.py:199`). SHADOW/PAPER permits uncalibrated context but records it; SOL−8%/1h catastrophic gate remains (`strategies/evaluate.py:17`, `:50`). This permission does not verify missing council inputs. |
| Rejections | Counterfactual tracking retains rejected identities and5m/15m/1h/6h/24h outcomes; missing outcomes remain NO_DATA; deterministic10%1h sample receives a round-trip check (`discovery/counterfactual.py:1`, `:24`). NO_DATA is neither zero return nor proven total loss. |
| Stocks | No equity provider, exchange calendars, adjusted corporate-action series, delisting returns, sector benchmarks, quotes or borrow/funding data in current adapters. Equity research can motivate hypotheses; an equity trading implementation is a separate data project. |

Current candle targeting selects recent surviving candidates and prioritizes held positions. It is not an unbiased historical universe. Freeze admission membership and retain failed/untradeable/delisted-from-watchlist members prospectively before making cross-sectional claims. Never stitch two pools or provider series across migration without an explicitly versioned identity/continuity rule. Backfilled bars received today cannot be treated as known by yesterday's decision.

## Budgets and laptop implications

- Configuration bounds tracked tokens300; discovery2assessments/30s, reassessment300s; raw unlinked retention14days (`config/default.toml:57`, `:73`). Defaults preserve $100,000 PAPER learning cash, $100 sizing base, ten learning positions and a separate conservative replica; these are not real account capital (`config/default.toml:11`).
- Shared free rate limits are GeckoTerminal8/min and2/15s; DexScreener120/min configured versus300/min documented; Jupiter50/min with key or25/min without; all costs0 (`config/providers.toml:30`, `:75`, `:114`). These are ceilings, not reserved capacity. Jupiter exit marking can consume≈24/min for ten positions at25s intervals before entry/research calls.
- Token OHLCV already consumes up to2GT/min and SOL reference1/min, discovery2/min, trending0.5/min. A first council version should add **zero network calls**; normalized SOL persistence can reuse existing payloads. New quote-probing research must be globally budgeted and yield to exits, not create a second client/limit bucket.
- Service memory ceilings are engine600M, guardian200M, dashboard200M, backup200M (`ops/services.py:46`). They are configured ceilings, not measured Dell usage. A bounded20–40asset rolling numeric panel and CPU-only regressions fit the intended envelope; benchmark actual RSS/time before raising cohort size.
- No annual storage forecast is defensible from row counts alone. Immutable bars/raw evidence are retained; decision raw digests are pinned forever (`storage/raw_store.py:1`). Measure bytes/newrow and compressed raw bytes/day on laptop, include indexes/WAL/24hourly+7dailybackup multiplication, and use a disk budget with an explicit stop threshold. A forecast must state cohort size and observation cadence.
- Existing stats helpers chunk stationary-bootstrap indices at2million elements (~16MB indices), but dependence should be resampled by **calendar blocks jointly across all arms**, not independently resampling advisor-selected versus primary trades (`stats/core.py:81`).

## Safe prospective integration plan (not implemented here)

1. Add `src/desk/contracts/advisory.py` and `src/desk/council/{specs,panel,residual,reversal,evaluate}.py` with immutable specs, strict timestamp checks and no provider/trading dependencies. Persist signed direction, horizon, SUPPORT/CAUTION/OPPOSE/ABSTAIN, missingness/freshness, evidence count, uncertainty, shared dependencies, input/spec/code hashes and source row/raw references.
2. Append a research migration after0003 for append-only council specs/runs/advisories and immutable frozen membership. Do not edit historical migrations. The existing `research.experiments` table is append-only and suitable for preregistration/report manifests (`storage/migrations/research/0001_init.sql:3`). Link advisories to existing decision hash rather than changing primary ranking/result hashing.
3. Append a market migration after0007 for normalized benchmark intervals (instrument/venue/source/timeframe/event start/end/first received/raw digest), or a generic interval-evidence table with source-specific identity. Reuse the existing SOL request without extra network traffic. A raw-response digest alone is insufficient if its reception time is discarded during reconstruction.
4. Add an opt-in read-only SHADOW council runner/CLI; separate bounded writes to research.db only. It reads original decision contexts (`ops/shadow.py:25`) and evidence received no later than decision. Collect after the primary evaluation; do not import council code into signer/guardian/risk or alter primary decisions. Replay should compare unchanged primary hashes with and without council success/failure.
5. Minimal first version: data coverage/advisory abstention reports, frozen specs and one clean synchronized historical panel. No rank/filter/size influence. Add residual5m returns only after benchmark semantics pass. Add shock/recovery sequences only when existing quotes genuinely meet the spec; otherwise ABSTAIN.
6. Add report-only API/UI after core records reproduce; bounded histories/shared existing SSE. Never run regression/bootstrap on each dashboard refresh.
7. Tests should cover unavailable/future/revised bars; cohort membership; gaps; exactpool/mint matching; vanishing sell routes; invalid shock recovery; singular/unstable beta; immutable specs; hash reproduction; council failure preserving primary decisions, ledger and safety controls; budget starvation; calendar-block paired ablations. Fixtures verify semantics, not profitability.

Existing seams: `storage/shadow_repo.py:63` already reproduces primary decisions and stores immutable input/spec/result hashes; `ops/replay.py` streams decisions. `stats/evidence.py:62` persists confidence reports, but baseline comparisons are not the new matched-capital four-arm council experiment. `models/research.py:99` describes its all-trades reference as B1-like; do not equate that with an actual matched random-eligible account. `risk/sizing.py:1` is explicitly an unconnected confidence-sizing foundation. Production BUY remains denied without trusted authorization (`signer/service.py:192`).

## Acceptance remains prospective

`PROGRESS.md:13` and `CONTINUATION_HANDOFF.md:112` record elapsed-time collection/SHADOW/PAPER and Dell acceptance outstanding. The research council needs its own genuine prospective sample, overlap/dependence assessment, and net-cost ablations. Its source specifications, synthetic tests, and reconstructed history cannot close those requirements or justify real-money activation.
