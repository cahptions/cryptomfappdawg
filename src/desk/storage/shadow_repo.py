"""Immutable strategy/input evidence and SHADOW-only decisions; no money-ledger writes."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from desk.contracts.strategy import Decision, DecisionContext, StrategySpec
from desk.core.clock import to_iso
from desk.core.hashing import canonical_json, fingerprint
from desk.core.ids import new_id
from desk.storage.db import StorageError, transaction


class ShadowRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def register_specs(self, specs: tuple[StrategySpec, ...], at: datetime) -> None:
        """A name/version is permanent, including before first use. Children retain ancestry."""
        with transaction(self.conn):
            for spec in specs:
                spec = StrategySpec.model_validate(spec.model_dump(mode="python"))
                content = canonical_json(spec.model_dump(mode="json"))
                existing = self.conn.execute(
                    "SELECT spec_hash, content_json FROM strategy_versions WHERE strategy_id = ? AND version = ?",
                    (spec.strategy_id, spec.version)).fetchone()
                if existing is not None:
                    if existing["spec_hash"] != spec.spec_hash or existing["content_json"] != content:
                        raise StorageError("strategy version is immutable; create a new version with parent_version")
                    continue
                prior = self.conn.execute(
                    "SELECT 1 FROM strategy_versions WHERE strategy_id = ? LIMIT 1", (spec.strategy_id,)).fetchone()
                if prior is not None:
                    parent = self.conn.execute(
                        "SELECT version FROM strategy_versions WHERE strategy_id = ? AND "
                        "(strategy_id || ':' || version || ':' || spec_hash) = ?",
                        (spec.strategy_id, spec.parent_version)).fetchone()
                    if parent is None or spec.version <= int(parent["version"]):
                        raise StorageError("a revised strategy requires an existing parent_version")
                elif spec.parent_version is not None:
                    raise StorageError("parent strategy version does not exist")
                self.conn.execute(
                    "INSERT INTO strategy_versions VALUES (?, ?, ?, ?, ?, ?)",
                    (spec.spec_hash, spec.strategy_id, spec.version, spec.parent_version, to_iso(at), content))

    def start_run(self, at: datetime, settings: dict[str, Any]) -> str:
        run_id = new_id("shadow")
        self.conn.execute("INSERT INTO shadow_runs(id, started_at, settings_json) VALUES (?, ?, ?)",
                          (run_id, to_iso(at), canonical_json(settings)))
        return run_id

    def finish_run(self, run_id: str, at: datetime, stats: dict[str, Any]) -> None:
        changed = self.conn.execute(
            "UPDATE shadow_runs SET ended_at = ?, stats_json = ? WHERE id = ? AND ended_at IS NULL",
            (to_iso(at), canonical_json(stats), run_id)).rowcount
        if changed != 1:
            raise StorageError("unknown or already completed SHADOW run")

    def save(self, run_id: str, context: DecisionContext, decision: Decision) -> str:
        from desk.strategies import evaluate_decision

        run = self.run(run_id)
        if run is None or run["ended_at"] is not None:
            raise StorageError("decisions require an active SHADOW run")
        specs = tuple(StrategySpec.model_validate(spec) for spec in run["settings"]["specs"])
        try:
            expected = evaluate_decision(decision.candidate_id, context, specs, seed=int(run["settings"]["seed"]),
                                         evaluator_version=run["settings"].get("evaluator_version", "shadow-rules-v1"))
        except ValueError as exc:
            raise StorageError(f"the run's evaluator cannot be reproduced: {exc}") from exc
        if expected != decision:
            raise StorageError("decision does not reproduce from its recorded inputs and strategy settings")
        for ranked in decision.ranking:
            if self.conn.execute("SELECT 1 FROM strategy_versions WHERE spec_hash = ?",
                                 (ranked.spec_hash,)).fetchone() is None:
                raise StorageError("a decision references an unregistered strategy version")
        snapshot = context.features
        if decision.as_of != snapshot.as_of:
            raise StorageError("decision time differs from its input snapshot")
        identity = self.conn.execute("SELECT mint, pool_address FROM discoveries WHERE id = ?",
                                     (decision.candidate_id,)).fetchone()
        if identity is None or (identity["mint"], identity["pool_address"]) != (snapshot.mint, snapshot.pool_address):
            raise StorageError("decision snapshot does not match candidate identity")
        snapshot_json = canonical_json(snapshot.model_dump(mode="json"))
        context_json = canonical_json(context.model_dump(mode="json"))
        result_json = canonical_json(decision.model_dump(mode="json"))
        snapshot_hash = fingerprint(snapshot.model_dump(mode="json"))
        record_id = "decision_" + fingerprint({"run": run_id, "candidate": decision.candidate_id,
                                               "as_of": to_iso(decision.as_of)})
        with transaction(self.conn):
            existing = self.conn.execute("SELECT context_json, result_json FROM shadow_decisions WHERE id = ?",
                                         (record_id,)).fetchone()
            if existing is not None:
                if existing["context_json"] != context_json or existing["result_json"] != result_json:
                    raise StorageError("conflicting decision for an existing observation")
                return record_id
            self.conn.execute(
                "INSERT OR IGNORE INTO feature_snapshots VALUES (?, ?, ?, ?, ?, ?)",
                (snapshot_hash, snapshot.mint, snapshot.pool_address, to_iso(snapshot.as_of),
                 snapshot.feature_set_version, snapshot_json))
            self.conn.execute(
                "INSERT INTO shadow_decisions VALUES (?, ?, ?, ?, 'SHADOW', ?, ?, ?, ?, ?, ?)",
                (record_id, run_id, decision.candidate_id, to_iso(decision.as_of), snapshot_hash,
                 decision.inputs_hash, decision.config_hash, decision.decision_hash, context_json, result_json))
            self.conn.executemany("UPDATE raw_responses SET keep = 1 WHERE sha256 = ?",
                                  ((ref,) for ref in snapshot.raw_refs))
        return record_id

    def run(self, run_id: str | None = None) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM shadow_runs WHERE id = ?", (run_id,)).fetchone() if run_id else (
            self.conn.execute("SELECT * FROM shadow_runs ORDER BY started_at DESC, id DESC LIMIT 1").fetchone())
        if row is None:
            return None
        return {**dict(row), "settings": json.loads(row["settings_json"]),
                "stats": json.loads(row["stats_json"] or "{}")}

    def decisions(self, run_id: str) -> Iterator[sqlite3.Row]:
        """Stream records; replay does not load a day's decisions into RAM."""
        yield from self.conn.execute(
            "SELECT d.*, f.content_json AS feature_json FROM shadow_decisions d JOIN feature_snapshots f "
            "ON f.snapshot_hash = d.snapshot_hash WHERE d.run_id = ? ORDER BY d.as_of, d.id", (run_id,))

    def report(self, run_id: str | None = None) -> dict[str, Any]:
        run = self.run(run_id)
        if run is None:
            return {"run": None, "decisions": 0, "chosen": {}}
        rows = self.conn.execute(
            "SELECT json_extract(result_json, '$.chosen'), COUNT(*) FROM shadow_decisions "
            "WHERE run_id = ? GROUP BY json_extract(result_json, '$.chosen')", (run["id"],))
        chosen = {str(row[0]): int(row[1]) for row in rows}
        settings = {key: value for key, value in run["settings"].items() if key != "specs"}
        settings["strategy_versions"] = [StrategySpec.model_validate(spec).version_id
                                         for spec in run["settings"]["specs"]]
        summary = {"id": run["id"], "started_at": run["started_at"], "ended_at": run["ended_at"],
                   "settings": settings, "stats": run["stats"]}
        return {"run": summary, "decisions": sum(chosen.values()), "chosen": chosen}
