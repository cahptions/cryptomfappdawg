"""Evidence council (research/council): contract, frozen specs, the two calculations, causal panels, benchmark minutes.

Synthetic fixtures check calculation and causality semantics only; they say nothing about profitability.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

from desk.contracts.advisory import Advisory
from desk.contracts.market import OhlcvBar
from desk.core.clock import to_epoch_millis, to_iso
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.council.benchmark import reconstruct_from_raw, save_benchmark_bars
from desk.council.brc import brc
from desk.council.ecdr import ecdr, required_closes
from desk.council.panel import BAR_MS, aligned, benchmark_bars, token_bars
from desk.council.specs import BRC, COMMON, ECDR, SPECS, spec_sha256
from desk.storage.databases import Databases
from desk.storage.raw_store import RawStore

SOL_POOL = "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"
T0 = datetime(2026, 10, 15, tzinfo=UTC)
COUNCIL = REPO_ROOT / "research" / "council"


# ── contract and specifications ──────────────────────────────────────────────────────────────────────
def advisory(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "advisory_id": "adv_1", "advisor_id": "C1_BRC", "spec_version": 1, "spec_sha256": "a" * 64,
        "code_sha256": "b" * 64, "primary_decision_hash": "c" * 64, "primary_action": "NO_TRADE",
        "asset": {"profile": "solana_meme_15m", "instrument_id": "mint", "pool_or_venue": "pool",
                  "source": "geckoterminal.ohlcv", "price_currency": "USD", "account_numeraire": "SOL",
                  "membership_id": "solana_meme_15m:2026-10-19"},
        "decision_at": "2026-10-19T12:00:00.000000Z", "computed_at": "2026-10-19T12:00:01.000000Z",
        "latest_event_end": None, "latest_input_available_at": None, "expires_at": "2026-10-19T12:15:00.000000Z",
        "signal_direction": "NEUTRAL", "stance": "CAUTION", "candidate_signal": {"residual_z": "0.5"},
        "horizon": {"maximum": 28800, "unit": "SECONDS"}, "rationale_codes": ["NO_RESIDUAL_CONTINUATION"],
        "inputs": [], "freshness": {"status": "OK", "missing": [], "stale": []},
        "evidence": {"training_bars": 288, "signal_bars": 16, "quote_pairs": 0, "forward_closed_trades": 0,
                     "effective_observations": None},
        "uncertainty": {"reason": "diagnostic only"}, "regime": {"status": "missing", "label": None},
        "shared_dependencies": ["sol_usd_benchmark"],
        "economics": {"status": "UNMEASURED", "ticket_raw": None, "roundtrip_cost_fraction": None},
        "input_sha256": "d" * 64}
    return base | changes


def test_the_contract_matches_the_research_schema_and_refuses_what_it_forbids() -> None:
    schema = json.loads((COUNCIL / "advisory.schema.json").read_text())
    record = Advisory.model_validate(advisory()).model_dump(mode="json")
    assert sorted(schema["required"]) == sorted(record)
    assert set(schema["properties"]["advisor_id"]["enum"]) == {"C1_BRC", "C2_ECDR"}
    assert record["mode"] == "SHADOW" and record["influence"] == "NONE" and record["economics"]["expected_net_return"] is None
    late_input = {"store": "market.quotes", "row_id": "1", "raw_sha256": None,
                  "event_end": "2026-10-19T12:00:00.000000Z", "first_available_at": "2026-10-19T12:00:00.500000Z"}
    for bad in ({"mode": "LIVE"}, {"influence": "SIZING"}, {"advisor_id": "C3_X"},
                {"candidate_signal": {"probability": "0.7"}}, {"candidate_signal": {"residual_z": "1e-3"}},
                {"inputs": [late_input]}, {"computed_at": "2026-10-19T11:59:59.000000Z"},
                {"stance": "SUPPORT", "signal_direction": "LONG"},       # SUPPORT without qualified economics
                {"economics": {"status": "UNMEASURED", "ticket_raw": "0.1", "roundtrip_cost_fraction": None}}):
        with pytest.raises(ValidationError):
            Advisory.model_validate(advisory(**bad))


def test_exactly_two_frozen_advisors_whose_values_are_the_research_profile() -> None:
    research = json.loads((COUNCIL / "specifications.json").read_text())
    profile = research["profiles"]["solana_meme_15m"]
    assert list(SPECS) == ["C1_BRC", "C2_ECDR"] == [a["id"] for a in research["advisors"]]
    for key in ("train_returns", "signal_returns", "skip_returns", "required_closed_prices", "max_hold_seconds",
                "beta_min", "beta_max", "max_half_beta_difference"):
        assert str(BRC[key]) == str(profile["brc"][key]), key
    for key in ("relative_return_lookback", "volume_lookback", "max_abs_benchmark_event_return", "max_hold_seconds",
                "event_expiry_seconds"):
        assert str(ECDR[key]) == str(profile["ecdr"][key]), key
    assert COMMON["cost_ceiling_fraction"] == profile["cost_ceiling_fraction"]
    assert COMMON["additional_network_requests"] == 0 and COMMON["influence"] == "NONE"
    assert spec_sha256(BRC) != spec_sha256({**BRC, "support_score_min": "1.5"})   # any change is a new identity


# ── C1: the calculation against an independent fit ───────────────────────────────────────────────────
def series(n: int, beta: float, alpha: float, noise: float, seed: int, extra: dict[int, float] | None = None
           ) -> tuple[list[Decimal], list[Decimal]]:
    rng = random.Random(seed)
    sol, token = [Decimal(100)], [Decimal(1)]
    for j in range(1, n):
        rb = rng.gauss(0, 0.004)
        ri = alpha + beta * rb + rng.gauss(0, noise) + (extra or {}).get(j, 0.0)
        sol.append(sol[-1] * Decimal(str(np.exp(rb))))
        token.append(token[-1] * Decimal(str(np.exp(ri))))
    return token, sol


def test_c1_matches_an_independent_least_squares_fit_and_keeps_its_windows_apart() -> None:
    token, sol = series(306, beta=1.3, alpha=0.0003, noise=0.006, seed=7)
    result = brc(token, sol, BRC)
    r_i = np.diff(np.log([float(p) for p in token]))
    r_b = np.diff(np.log([float(p) for p in sol]))
    beta, alpha = np.polyfit(r_b[:288], r_i[:288], 1)
    residuals = r_i[:288] - alpha - beta * r_b[:288]
    scale = 1.4826 * np.median(np.abs(residuals - np.median(residuals)))
    z = (r_i[288:304] - alpha - beta * r_b[288:304]).sum() / (scale * 4)
    assert result.valid and result.beta is not None and result.z is not None and result.scale is not None
    assert float(result.beta) == pytest.approx(beta, rel=1e-9) and float(result.scale) == pytest.approx(scale, rel=1e-9)
    assert float(result.z) == pytest.approx(z, rel=1e-9)
    # the signal window never enters the fit, and the skipped latest return never enters the score
    moved_signal = token[:290] + [p * Decimal("1.05") for p in token[290:]]
    assert brc(moved_signal, sol, BRC).beta == result.beta
    moved_last = [*token[:305], token[305] * Decimal("0.5")]
    assert brc(moved_last, sol, BRC).z == result.z and brc(moved_last, sol, BRC).skipped_relative_return < 0


@pytest.mark.parametrize("case,reason", [
    ("flat_benchmark", "BENCHMARK_VARIANCE_LOW"), ("exact_fit", "RESIDUAL_SCALE_LOW"),
    ("beta_out_of_range", "BETA_UNSTABLE"), ("beta_shift", "BETA_UNSTABLE")])
def test_c1_abstains_on_unusable_fits(case: str, reason: str) -> None:
    if case == "flat_benchmark":
        token, _ = series(306, 1.0, 0, 0.006, 1)
        sol = [Decimal(100)] * 306
    elif case == "exact_fit":
        token, sol = series(306, 1.0, 0.0, 0.0, 2)
    elif case == "beta_out_of_range":
        token, sol = series(306, 6.0, 0.0, 0.006, 3)
    else:
        _, sol = series(306, 0.5, 0.0, 0.002, 4)
        rb = np.diff(np.log([float(p) for p in sol]))
        token = [Decimal(1)]
        for j, r in enumerate(rb, start=1):
            token.append(token[-1] * Decimal(str(np.exp((0.5 if j <= 144 else 3.5) * r))))
    assert reason in brc(token, sol, BRC).reasons


# ── C2: the event test ───────────────────────────────────────────────────────────────────────────────
def test_c2_detects_an_idiosyncratic_dislocation_and_nothing_else() -> None:
    n = required_closes(ECDR)
    token, sol = series(n, 1.0, 0.0, 0.006, 11, extra={n - 1: -0.08})
    volumes = [Decimal(1000)] * (n - 1) + [Decimal(5000)]
    shock = ecdr(token, sol, volumes, ECDR)
    assert shock.dislocation and shock.shock_z is not None and shock.shock_z <= -3
    assert not ecdr(token, sol, [Decimal(1000)] * n, ECDR).dislocation              # no volume confirmation
    crash = [*sol[:-1], sol[-1] * Decimal("0.97")]                                  # SOL itself moved 3%
    assert not ecdr(token, crash, volumes, ECDR).dislocation
    calm, sol2 = series(n, 1.0, 0.0, 0.006, 12)
    assert not ecdr(calm, sol2, volumes, ECDR).dislocation
    flat, same = series(n, 1.0, 0.0, 0.0, 13)
    assert "RESIDUAL_SCALE_LOW" in ecdr(flat, same, volumes, ECDR).reasons


# ── causal panels and benchmark minutes ──────────────────────────────────────────────────────────────
@pytest.fixture
def dbs(tmp_path: Path):
    databases = Databases.open(DeskPaths(tmp_path / "data", REPO_ROOT / "config", tmp_path / "none.env"))
    yield databases
    databases.close()


def bar(start: datetime, close: str, received: datetime, pool: str = SOL_POOL) -> OhlcvBar:
    return OhlcvBar(pool_address=pool, timeframe="minute:1", ts=start, open=Decimal(close), high=Decimal(close),
                    low=Decimal(close), close=Decimal(close), volume_usd=Decimal(10), source="geckoterminal.ohlcv",
                    observed_at=received)


def test_benchmark_minutes_count_only_once_closed_beyond_the_provider_cache(dbs: Databases) -> None:
    received = T0 + timedelta(minutes=3, seconds=30)
    bars = [bar(T0, "100", received), bar(T0 + timedelta(minutes=1), "101", received),
            bar(T0 + timedelta(minutes=2), "102", received)]    # closed 30 s ago: may be a cached partial minute
    assert save_benchmark_bars(dbs.market, SOL_POOL, bars, "e" * 64) == 2
    later = [bar(T0, "999", received + timedelta(minutes=5)), bar(T0 + timedelta(minutes=2), "102",
                                                                   received + timedelta(minutes=5))]
    assert save_benchmark_bars(dbs.market, SOL_POOL, later, "f" * 64) == 1           # the first copy stays
    rows = dbs.market.execute("SELECT bar_start_ms, close, first_received_at FROM benchmark_bars ORDER BY 1").fetchall()
    assert [r["close"] for r in rows] == ["100", "101", "102"] and rows[2]["first_received_at"] == to_iso(
        received + timedelta(minutes=5))
    elsewhere = bar(T0, "1", received, pool="So11111111111111111111111111111111111111112")
    assert save_benchmark_bars(dbs.market, SOL_POOL, [elsewhere], "1" * 64) == 0       # another pool's minute


def test_benchmark_history_is_rebuilt_from_raw_payloads_with_their_original_receipt_times(dbs: Databases) -> None:
    raw = RawStore(dbs.paths.raw_dir, dbs.market)

    def payload(base: str, closes: list[int], start: datetime) -> bytes:
        rows = [[int((start + timedelta(minutes=i)).timestamp()), c, c, c, c, 5.0] for i, c in enumerate(closes)]
        usdc = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        return json.dumps({"data": {"attributes": {"ohlcv_list": rows}},
                           "meta": {"base": {"address": base}, "quote": {"address": usdc}}}).encode()

    def store(body: bytes, received: datetime) -> None:
        digest = hashlib.sha256(body).hexdigest()
        target = raw.raw_dir / raw._rel_path(digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(target, "wb") as fh:
            fh.write(body)
        dbs.market.execute("INSERT INTO raw_responses(sha256, provider, endpoint, http_status, received_at, size_bytes,"
                           " rel_path) VALUES (?, 'geckoterminal', 'ohlcv', 200, ?, ?, ?)",
                           (digest, to_iso(received), len(body), str(raw._rel_path(digest))))
        dbs.market.execute("INSERT INTO market_context(observed_at, observed_at_ms, version, coverage_json, raw_sha256)"
                           " VALUES (?, ?, 'test', '{}', ?)", (to_iso(received), to_epoch_millis(received), digest))

    sol = "So11111111111111111111111111111111111111112"
    store(payload(sol, [100, 101, 102], T0), T0 + timedelta(minutes=3, seconds=30))
    store(payload(sol, [101, 102, 103], T0 + timedelta(minutes=1)), T0 + timedelta(minutes=6))
    store(payload("DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", [5, 5, 5], T0), T0 + timedelta(minutes=7))
    report = reconstruct_from_raw(dbs.market, raw, SOL_POOL)
    assert report == {"payloads": 3, "unavailable": 0, "foreign": 1, "bars_added": 4}
    rows = dbs.market.execute("SELECT bar_start_ms, first_received_at FROM benchmark_bars ORDER BY 1").fetchall()
    # minutes 0 and 1 were usable in the first payload; minute 2 had closed only 90 s earlier (inside the provider
    # cache), so it counts from the second payload, as does minute 3
    assert [r["first_received_at"] for r in rows] == [to_iso(T0 + timedelta(minutes=3, seconds=30))] * 2 + [
        to_iso(T0 + timedelta(minutes=6))] * 2


def test_panels_use_only_complete_bars_received_by_the_decision(dbs: Databases) -> None:
    from desk.storage.market_repo import MarketRepository
    from tests.unit.test_discovery import pool_snapshot
    from tests.unit.test_security import MINT, POOL

    MarketRepository(dbs.market).save_pool_snapshots([pool_snapshot()])
    start = T0
    rows = []
    for i in range(12):                                     # four 15-minute bars of 5-minute bars
        at = start + timedelta(minutes=5 * i)
        received = at + timedelta(minutes=5, seconds=30)
        if i == 7:
            received = at + timedelta(hours=10)             # backfilled long after: unknown at the decision
        if i == 10:
            continue                                        # no swaps in this interval: never filled
        rows.append((POOL, MINT, to_iso(at), to_epoch_millis(at), "1", "1", "1", str(1 + i / 100), "10",
                     to_iso(received), "a" * 64))
    dbs.market.executemany("INSERT INTO ohlcv_bars(pool_address, mint, timeframe, bar_at, bar_at_ms, open, high, low,"
                           " close, volume_usd, observed_at, raw_sha256, source) VALUES (?, ?, '5m', ?, ?, ?, ?, ?, ?,"
                           " ?, ?, ?, 'geckoterminal.ohlcv')", rows)
    decision = to_epoch_millis(start + timedelta(hours=1, minutes=1))
    bars = token_bars(dbs.market, POOL, MINT, decision, 10)
    # the third 15-minute bar has a member received only later; the fourth lacks a member altogether
    assert sorted(bars) == [to_epoch_millis(start), to_epoch_millis(start) + BAR_MS]
    later = token_bars(dbs.market, POOL, MINT, decision + 11 * 3_600_000, 60)
    assert to_epoch_millis(start) + 2 * BAR_MS in later and to_epoch_millis(start) + 3 * BAR_MS not in later
    assert token_bars(dbs.market, POOL, "So11111111111111111111111111111111111111112", decision, 10) == {}
    minutes = [bar(start + timedelta(minutes=m), str(100 + m), start + timedelta(minutes=m + 2)) for m in range(60)]
    save_benchmark_bars(dbs.market, SOL_POOL, minutes, "e" * 64)
    bench = benchmark_bars(dbs.market, SOL_POOL, decision, 10)
    assert sorted(bench) == [to_epoch_millis(start) + k * BAR_MS for k in range(4)]
    assert bench[to_epoch_millis(start)].close == Decimal(114)
    panel = aligned(later, bench)
    assert panel.length == 3 and panel.token[0].start_ms == to_epoch_millis(start)   # stops at the missing bar
