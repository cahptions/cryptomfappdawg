"""Stream immutable recorded inputs through the same core; research results stay separate."""

from __future__ import annotations

import math
import sqlite3
from typing import Any

from desk.contracts.strategy import Decision, DecisionContext, StrategySpec
from desk.core.clock import to_iso, utc_now
from desk.core.hashing import canonical_json, fingerprint
from desk.core.ids import new_id
from desk.storage.shadow_repo import ShadowRepository
from desk.strategies import evaluate_decision
from desk.strategies.evaluate import EVALUATOR_VERSION, EVALUATORS


def replay_run(market: sqlite3.Connection, research: sqlite3.Connection, run_id: str | None = None, *,
               specs: tuple[StrategySpec, ...] | None = None, require_hours: float | None = None) -> dict[str, Any]:
    if require_hours is not None and (not math.isfinite(require_hours) or require_hours <= 0):
        raise ValueError("required hours must be finite and positive")
    repo = ShadowRepository(market)
    run = repo.run(run_id)
    if run is None:
        raise ValueError("no recorded SHADOW run")
    original = tuple(StrategySpec.model_validate(spec) for spec in run["settings"]["specs"])
    chosen_specs = original if specs is None else specs
    seed = int(run["settings"]["seed"])
    recorded_evaluator = run["settings"].get("evaluator_version", "shadow-rules-v1")
    # a run is reproduced with the evaluator that recorded it; an experiment changes only the specs
    evaluator = recorded_evaluator if recorded_evaluator in EVALUATORS else EVALUATOR_VERSION
    experiment = specs is not None
    checked, mismatches = 0, 0
    action_changes = 0
    samples: list[dict[str, str]] = []
    first_as_of, last_as_of = None, None
    maximum_gap = 0.0
    input_failures = 0
    for row in repo.decisions(run["id"]):
        context = DecisionContext.model_validate_json(row["context_json"])
        original_decision = evaluate_decision(row["candidate_id"], context, original, seed=seed,
                                              evaluator_version=evaluator)
        decision = original_decision if not experiment else evaluate_decision(
            row["candidate_id"], context, chosen_specs, seed=seed, evaluator_version=evaluator)
        recorded = Decision.model_validate_json(row["result_json"])
        if (recorded.decision_hash != row["decision_hash"] or recorded.inputs_hash != row["inputs_hash"]
                or recorded.config_hash != row["config_hash"]):
            input_failures += 1
        if original_decision != recorded:
            input_failures += 1
        if (fingerprint(context.features.model_dump(mode="json")) != row["snapshot_hash"]
                or canonical_json(context.features.model_dump(mode="json")) != row["feature_json"]):
            input_failures += 1
        if decision.chosen != recorded.chosen:
            action_changes += 1
        checked += 1
        if decision.decision_hash != row["decision_hash"]:
            mismatches += 1
            if len(samples) < 20:
                samples.append({"decision_id": row["id"], "original_hash": row["decision_hash"],
                                "replay_hash": decision.decision_hash,
                                "original_chosen": recorded.chosen, "replay_chosen": decision.chosen})
        if first_as_of is None:
            first_as_of = context.features.as_of
        if last_as_of is not None:
            maximum_gap = max(maximum_gap, (context.features.as_of - last_as_of).total_seconds())
        last_as_of = context.features.as_of
    failures = []
    if recorded_evaluator not in EVALUATORS:
        failures.append(f"run was recorded by evaluator {recorded_evaluator}; this version of the desk can only "
                        f"reproduce {', '.join(EVALUATORS)}")
    if not checked:
        failures.append("no recorded decisions")
    if not experiment and mismatches:
        failures.append("replay differs from recorded decisions")
    if input_failures:
        failures.append("stored input/result integrity check failed")
    if require_hours is not None:
        stats = run["stats"]
        if run["ended_at"] is None or not stats.get("completed_requested_duration"):
            failures.append("SHADOW run did not complete its requested duration")
        if stats.get("elapsed_monotonic_seconds", 0) < require_hours * 3600:
            failures.append(f"SHADOW run shorter than {require_hours:g} hours")
        if stats.get("unhandled_exceptions", 0):
            failures.append("SHADOW run recorded unhandled exceptions")
        # A long timer with one observation is not evidence of sustained decisions.
        coverage = 0.0 if first_as_of is None or last_as_of is None else (last_as_of - first_as_of).total_seconds()
        interval = float(run["settings"]["interval_seconds"])
        if coverage < max(0, require_hours * 3600 - min(interval * 2, 60)):
            failures.append("recorded decisions do not cover the required session")
        if maximum_gap > max(interval * 3, 60):
            failures.append("recorded decisions contain an unexplained collection gap")
        if experiment:
            failures.append("threshold experiments cannot satisfy unchanged replay acceptance")
    result: dict[str, Any] = {"kind": "REPLAY", "run_id": run["id"], "experiment": experiment,
                              "checked": checked, "identical": checked - mismatches, "changed": mismatches,
                              "action_changed": action_changes,
                              "samples": samples, "input_integrity_failures": input_failures,
                              "maximum_decision_gap_seconds": maximum_gap, "required_hours": require_hours,
                              "passed": not failures, "failures": failures}
    experiment_id = new_id("replay")
    result["experiment_id"] = experiment_id
    payload = {**result, "seed": seed, "evaluator_version": evaluator, "current_evaluator_version": EVALUATOR_VERSION,
               "original_specs": [spec.model_dump(mode="json") for spec in original],
               "replay_specs": [spec.model_dump(mode="json") for spec in chosen_specs]}
    research.execute("INSERT INTO experiments(id, created_at, kind, payload_json) VALUES (?, ?, 'REPLAY', ?)",
                     (experiment_id, to_iso(utc_now()), canonical_json(payload)))
    return result
