"""M6-13: point-in-time scoring of recorded PAPER decisions and out-of-sample benchmarking against B0/B1/B2.

For each decision, the model is trained only on trades whose label (close) was known strictly before the decision
time. Scores are logged with model version, training window, feature-set version and an inputs hash. They have
zero influence: no trading module imports this package and ``research.model_influence`` only accepts "none".
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

import numpy as np

from desk.core.clock import parse_iso8601, to_iso
from desk.core.hashing import canonical_json, fingerprint, sha256_hex
from desk.models.logistic import LogisticModel, fit
from desk.stats.core import as_array, brier_score, expected_calibration_error, stationary_bootstrap_means

FEATURES = ("log_return_1h", "log_return_15m", "volume_acceleration", "liquidity_change_1h", "distance_from_high_6h",
            "roundtrip_cost_fraction", "distinct_buyers_1h")
MIN_TRAINING_TRADES = 100
REFIT_EVERY = 25
L2 = 1.0
MODEL_FAMILY = "logistic-l2-v1-" + sha256_hex(canonical_json({"features": FEATURES, "l2": L2,
                                                              "min": MIN_TRAINING_TRADES, "refit": REFIT_EVERY}))[:12]


def _vector(context: dict[str, Any]) -> list[float] | None:
    values = context["features"]["values"]
    out = []
    for name in FEATURES:
        item = values.get(name)
        if item is None or item.get("status") != "ok" or item.get("value") is None:
            return None                      # imputation is forbidden: the model abstains
        out.append(float(item["value"]))
    return out


def _decisions(ledger: sqlite3.Connection) -> list[dict[str, Any]]:
    out = []
    for row in ledger.execute("SELECT at, payload_json FROM paper_events WHERE kind = 'DECISION' ORDER BY id"):
        payload = json.loads(row["payload_json"])
        out.append({"at": parse_iso8601(row["at"]), "hash": payload["decision_hash"], "context": payload["context"],
                    "feature_set_version": payload["context"]["features"]["feature_set_version"]})
    return out


def _labelled(ledger: sqlite3.Connection, decisions: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for r in ledger.execute("SELECT decision_hash, closed_at, realized_pnl_lamports, ticket_lamports, strategy_id "
                            "FROM positions WHERE state = 'CLOSED' ORDER BY closed_at, id"):
        decision = decisions.get(r["decision_hash"])
        vector = None if decision is None else _vector(decision["context"])
        if vector is not None:
            rows.append({"closed_at": parse_iso8601(r["closed_at"]), "x": vector, "hash": r["decision_hash"],
                         "ret": r["realized_pnl_lamports"] / r["ticket_lamports"], "strategy": r["strategy_id"]})
    return rows


def score_decisions(ledger: sqlite3.Connection, research: sqlite3.Connection, now: datetime) -> dict[str, int]:
    decisions = _decisions(ledger)
    labelled = _labelled(ledger, {d["hash"]: d for d in decisions})
    done = {r[0] for r in research.execute("SELECT decision_hash FROM model_scores")}
    model: LogisticModel | None = None
    fitted_on = 0
    version = None
    stats = {"scored": 0, "abstained": 0}
    for decision in decisions:
        if decision["hash"] in done:
            continue
        training = [r for r in labelled if r["closed_at"] < decision["at"]]      # labels known before the decision
        if len(training) >= MIN_TRAINING_TRADES and (model is None or len(training) >= fitted_on + REFIT_EVERY
                                                     or len(training) < fitted_on):
            model = fit(np.array([r["x"] for r in training]), np.array([r["ret"] > 0 for r in training], dtype=float),
                        l2=L2)
            fitted_on = len(training)
            version = MODEL_FAMILY + "-" + fingerprint(model.weights)[:10]
            research.execute("INSERT OR IGNORE INTO experiments(id, created_at, kind, payload_json) VALUES (?, ?, 'MODEL', ?)",
                             (version, to_iso(now), canonical_json({"family": MODEL_FAMILY, "features": FEATURES,
                                                                    "weights": model.weights, "mean": model.mean,
                                                                    "scale": model.scale, "training_trades": fitted_on,
                                                                    "training_end": to_iso(training[-1]["closed_at"])})))
        usable = model is not None and len(training) >= MIN_TRAINING_TRADES
        vector = _vector(decision["context"])
        score = None if not usable or vector is None or model is None else float(model.predict(np.array([vector]))[0])
        research.execute(
            "INSERT INTO model_scores(decision_hash, decided_at, scored_at, model_version, training_end, training_trades,"
            " feature_set_version, inputs_hash, score) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (decision["hash"], to_iso(decision["at"]), to_iso(now), version or "none",
             to_iso(training[-1]["closed_at"]) if training else "", len(training), decision["feature_set_version"],
             fingerprint(vector), score))
        stats["scored" if score is not None else "abstained"] += 1
    return stats


def benchmark(ledger: sqlite3.Connection, research: sqlite3.Connection, *, seed: int = 0) -> dict[str, Any]:
    """Out-of-sample comparison: trades the model favoured vs all trades (B1-like), B0 (cash) and B2 trades."""
    scores = {r["decision_hash"]: r["score"] for r in research.execute(
        "SELECT decision_hash, score FROM model_scores WHERE score IS NOT NULL")}
    trades = [(r["decision_hash"], r["realized_pnl_lamports"] / r["ticket_lamports"], r["strategy_id"])
              for r in ledger.execute("SELECT * FROM positions WHERE state = 'CLOSED'")]
    scored = [(scores[h], ret) for h, ret, _ in trades if h in scores]
    if len(scored) < 30:
        return {"status": "INSUFFICIENT", "scored_trades": len(scored), "note": "needs >= 30 out-of-sample scored trades"}
    p = as_array([s for s, _ in scored])
    y = as_array([float(ret > 0) for _, ret in scored])
    base = float(y.mean())
    brier = brier_score(p, y)
    reference = brier_score(np.full(len(y), base), y)
    favoured = [ret for s, ret in scored if s >= 0.5]
    every = [ret for _, ret in scored]
    momentum = [ret for _, ret, strategy in trades if strategy == "B2"]
    lower = None
    if len(favoured) >= 10:
        delta = (stationary_bootstrap_means(as_array(favoured), resamples=2000, seed=seed)
                 - stationary_bootstrap_means(as_array(every), resamples=2000, seed=seed + 1))
        lower = float(np.quantile(delta, 0.05))
    return {"status": "EVALUATED", "scored_trades": len(scored), "brier": brier,
            "brier_skill_score": None if reference == 0 else 1 - brier / reference,
            "ece": expected_calibration_error(p, y), "base_rate": base,
            "mean_return_model_favoured": None if not favoured else float(np.mean(favoured)),
            "mean_return_all_scored (B1-like)": float(np.mean(every)), "mean_return_B0": 0.0,
            "mean_return_B2": None if not momentum else float(np.mean(momentum)),
            "favoured_minus_all_lower_95": lower, "influence": "none — scores never enter a decision"}
