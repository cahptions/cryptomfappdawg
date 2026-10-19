"""Bounded SHADOW research over the existing collector's data, without provider calls."""

from __future__ import annotations

import json
import math
import sqlite3
import time
from datetime import datetime
from typing import Any

from desk.contracts.discovery import DiscoveryReason, Outcome, SecurityAssessment
from desk.contracts.strategy import DecisionContext, StrategySpec
from desk.core.clock import to_iso, utc_now
from desk.features.builder import FeatureBuilder
from desk.ops.system import process_rss_mb
from desk.regime.classifier import classify_regime
from desk.storage.shadow_repo import ShadowRepository
from desk.strategies import builtin_specs, evaluate_decision


def decision_context(conn: sqlite3.Connection, candidate_id: str, as_of: datetime) -> DecisionContext:
    """Only append-only evidence received at or before as_of may determine historical eligibility."""
    iso = to_iso(as_of)
    candidate = conn.execute(
        "SELECT mint, pool_address FROM discoveries WHERE id = ? AND first_observed_at <= ?",
        (candidate_id, iso)).fetchone()
    if candidate is None or not candidate["pool_address"]:
        raise ValueError("candidate has no observed pool at decision time")
    observation = conn.execute(
        "SELECT reason FROM discovery_observations WHERE candidate_id = ? AND observed_at <= ? "
        "ORDER BY observed_at DESC, id DESC LIMIT 1", (candidate_id, iso)).fetchone()
    if observation is None:
        raise ValueError("candidate has no point-in-time discovery observation")
    reason = DiscoveryReason(observation["reason"])
    first = conn.execute(
        "SELECT MIN(observed_at) FROM discovery_observations WHERE candidate_id = ? AND reason = ? AND observed_at <= ?",
        (candidate_id, reason.value, iso)).fetchone()[0]
    latest = conn.execute(
        "SELECT * FROM stage_results WHERE candidate_id = ? AND decided_at <= ? ORDER BY decided_at DESC, id DESC LIMIT 1",
        (candidate_id, iso)).fetchone()
    outcome, assessed_at = Outcome.DEFER, None
    if latest is not None and latest["stage"] == 4:
        outcome = Outcome(latest["outcome"])
        row = conn.execute(
            "SELECT payload_json FROM security_assessments WHERE candidate_id = ? AND assessed_at = ? "
            "AND config_hash = ? ORDER BY id DESC LIMIT 1",
            (candidate_id, latest["decided_at"], latest["config_hash"])).fetchone()
        if row is not None:
            assessment = SecurityAssessment.model_validate_json(row["payload_json"])
            if assessment.mint == candidate["mint"] and assessment.outcome == outcome:
                assessed_at = assessment.assessed_at
            elif outcome is Outcome.PASS:
                outcome = Outcome.DEFER
        elif outcome is Outcome.PASS:
            outcome = Outcome.DEFER
    features = FeatureBuilder(conn).build(candidate["mint"], candidate["pool_address"], as_of)
    regime = classify_regime(features)
    # parse_iso8601 rejects naive values and preserves the canonical time boundary.
    from desk.core.clock import parse_iso8601

    return DecisionContext(features=features, security_outcome=outcome, security_assessed_at=assessed_at,
                           discovery_reason=reason, discovery_observed_at=parse_iso8601(first),
                           regime_label=None if regime.label is None else regime.label.value,
                           regime_status=regime.status, regime_version=regime.version)


class ShadowRunner:
    def __init__(self, conn: sqlite3.Connection, *, seed: int = 0, interval_seconds: float = 30,
                 max_candidates: int = 100, specs: tuple[StrategySpec, ...] | None = None) -> None:
        if not math.isfinite(interval_seconds) or interval_seconds <= 0 or not 1 <= max_candidates <= 1000:
            raise ValueError("positive finite interval and max_candidates between 1 and 1000 required")
        self.conn, self.seed, self.interval_seconds = conn, seed, interval_seconds
        self.max_candidates, self.specs = max_candidates, builtin_specs() if specs is None else specs
        self.repo, self.stopped, self.cursor = ShadowRepository(conn), False, ""

    def stop(self) -> None:
        self.stopped = True

    def cycle(self, run_id: str, as_of: datetime) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT id FROM discoveries WHERE pool_address != '' AND first_observed_at <= ? AND id > ? "
            "ORDER BY id LIMIT ?", (to_iso(as_of), self.cursor, self.max_candidates)).fetchall()
        if not rows and self.cursor:
            self.cursor = ""
            rows = self.conn.execute(
                "SELECT id FROM discoveries WHERE pool_address != '' AND first_observed_at <= ? ORDER BY id LIMIT ?",
                (to_iso(as_of), self.max_candidates)).fetchall()
        counts = {"decisions": 0, "signals": 0, "no_trade": 0}
        for row in rows:
            context = decision_context(self.conn, row["id"], as_of)
            decision = evaluate_decision(row["id"], context, self.specs, seed=self.seed)
            self.repo.save(run_id, context, decision)
            counts["decisions"] += 1
            counts["no_trade" if decision.chosen == "NO_TRADE" else "signals"] += 1
            self.cursor = row["id"]
        return counts

    def run(self, minutes: float) -> dict[str, Any]:
        if not math.isfinite(minutes) or minutes <= 0:
            raise ValueError("duration must be finite and positive")
        self.repo.register_specs(self.specs, utc_now())
        settings = {"seed": self.seed, "interval_seconds": self.interval_seconds,
                    "max_candidates": self.max_candidates, "requested_minutes": minutes,
                    "specs": [spec.model_dump(mode="json") for spec in self.specs]}
        run_id = self.repo.start_run(utc_now(), settings)
        stats: dict[str, Any] = {"run_id": run_id, "decisions": 0, "signals": 0, "no_trade": 0,
                                 "cycles": 0, "completed_requested_duration": False, "unhandled_exceptions": 0}
        started, next_cycle = time.monotonic(), time.monotonic()
        try:
            while not self.stopped and time.monotonic() - started < minutes * 60:
                if time.monotonic() >= next_cycle:
                    for key, count in self.cycle(run_id, utc_now()).items():
                        stats[key] += count
                    stats["cycles"] += 1
                    next_cycle = time.monotonic() + self.interval_seconds
                time.sleep(min(0.25, max(0, minutes * 60 - (time.monotonic() - started))))
            stats["completed_requested_duration"] = not self.stopped and time.monotonic() - started >= minutes * 60
        except BaseException:
            stats["unhandled_exceptions"] += 1
            raise
        finally:
            stats["elapsed_monotonic_seconds"] = time.monotonic() - started
            memory = process_rss_mb()
            stats["rss_mb"] = None if memory is None else round(memory[0], 1)
            stats["peak_rss_mb"] = None if memory is None else round(memory[1], 1)
            self.repo.finish_run(run_id, utc_now(), stats)
        return stats


def render_shadow(stats: dict[str, Any]) -> str:
    return json.dumps(stats, indent=2)
