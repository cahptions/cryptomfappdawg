"""M6-11 champion/challenger. A challenger replaces the champion only when the rules pass AND the operator
records a typed approval; history is append-only. Concurrent challengers are Holm-corrected (M6-12).

Rules for a challenger (strategy version, PAPER evidence): net expectancy and statistical reliability PASS in its
confidence matrix; if a champion exists, the challenger's mean net return exceeds the champion's with a one-sided
stationary-bootstrap p-value below 0.05 after Holm adjustment across all challengers evaluated together.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime
from typing import Any

import numpy as np

from desk.core.clock import to_iso
from desk.core.hashing import canonical_json
from desk.core.ids import new_id
from desk.stats.core import as_array, holm, stationary_bootstrap_means
from desk.stats.evidence import load_trades
from desk.stats.matrix import BASELINES, PASS, Evidence, Trade, build_matrix

ALPHA = 0.05


def current_champion(research: sqlite3.Connection) -> str | None:
    row = research.execute("SELECT strategy_version FROM champions ORDER BY id DESC LIMIT 1").fetchone()
    return None if row is None else str(row[0])


def _p_better(challenger: list[float], champion: list[float], seed: int, resamples: int) -> float:
    """One-sided p-value for mean(challenger) > mean(champion): share of bootstrap differences <= 0."""
    delta = (stationary_bootstrap_means(as_array(challenger), resamples=resamples, seed=seed)
             - stationary_bootstrap_means(as_array(champion), resamples=resamples, seed=seed + 1))
    return max(float(np.mean(delta <= 0)), 1 / resamples)


def evaluate(trades: list[Trade], champion: str | None, evidence: Evidence, *, seed: int = 0,
             resamples: int = 2000) -> dict[str, Any]:
    by_version: dict[str, list[Trade]] = defaultdict(list)
    for trade in trades:
        if trade.strategy_id not in BASELINES:
            by_version[trade.strategy_version].append(trade)
    champion_returns = [t.ret for t in by_version.get(champion or "", [])]
    rows: list[dict[str, Any]] = []
    for version, rows_ in sorted(by_version.items()):
        if version == champion:
            continue
        cells = {c["dimension"]: c["status"] for c in build_matrix(
            rows_, evidence, seed=seed, resamples=resamples,
            only=frozenset({"net_expectancy", "statistical_reliability"}))["cells"]}
        p = None
        if champion_returns and len(rows_) >= 30:
            p = _p_better([t.ret for t in rows_], champion_returns, seed, resamples)
        rows.append({"version": version, "strategy_id": rows_[0].strategy_id, "trades": len(rows_),
                     "mean_return": float(np.mean([t.ret for t in rows_])), "cells": cells, "p_better": p})
    tested = [r for r in rows if r["p_better"] is not None]
    for r, adjusted in zip(tested, holm([r["p_better"] for r in tested]), strict=True):
        r["p_better_holm"] = adjusted
    for r in rows:
        own = all(r["cells"].get(d) == PASS for d in ("net_expectancy", "statistical_reliability"))
        beats = champion is None or r.get("p_better_holm", 1.0) < ALPHA
        r["eligible_to_replace"] = own and beats
    return {"champion": champion, "alpha": ALPHA, "challengers": rows,
            "rule": "own expectancy+reliability PASS and (no champion or Holm-adjusted p < 0.05 vs champion)"}


def record_evaluation(ledger: sqlite3.Connection, research: sqlite3.Connection, now: datetime, *, seed: int = 0,
                      resamples: int = 2000, evidence: Evidence | None = None) -> dict[str, Any]:
    result = evaluate(load_trades(ledger), current_champion(research), evidence or Evidence(), seed=seed,
                      resamples=resamples)
    experiment = new_id("champ")
    research.execute("INSERT INTO experiments(id, created_at, kind, payload_json) VALUES (?, ?, 'CHAMPION_COMPARISON', ?)",
                     (experiment, to_iso(now), canonical_json(result)))
    result["experiment_id"] = experiment
    return result


def approve(research: sqlite3.Connection, version: str, confirmation: str, now: datetime, note: str = "") -> str:
    """Promote ``version`` after a recorded comparison that allows it and the exact typed confirmation."""
    if confirmation != f"PROMOTE {version}":
        raise ValueError(f"typed confirmation must be exactly: PROMOTE {version}")
    row = research.execute("SELECT id, payload_json FROM experiments WHERE kind = 'CHAMPION_COMPARISON' "
                           "ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()
    if row is None:
        raise ValueError("no recorded comparison; run `desk champion evaluate` first")
    comparison = json.loads(row["payload_json"])
    if comparison["champion"] != current_champion(research):
        raise ValueError("the champion changed since the last comparison; evaluate again")
    match = next((c for c in comparison["challengers"] if c["version"] == version), None)
    if match is None or not match["eligible_to_replace"]:
        raise ValueError("the latest comparison does not allow this version to replace the champion")
    approval = new_id("appr")
    research.execute("INSERT INTO approvals(id, created_at, kind, subject, confirmation, payload_json)"
                     " VALUES (?, ?, 'CHAMPION', ?, ?, ?)", (approval, to_iso(now), version, confirmation,
                                                            canonical_json({"note": note, "comparison": row["id"]})))
    research.execute("INSERT INTO champions(created_at, strategy_version, previous_version, approval_id, comparison_id)"
                     " VALUES (?, ?, ?, ?, ?)", (to_iso(now), version, comparison["champion"], approval, row["id"]))
    return approval


def history(research: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in research.execute("SELECT * FROM champions ORDER BY id")]
