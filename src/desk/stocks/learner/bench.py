"""The Learner's compute budget, measured (``desk stocks learner-bench``; docs/STOCKS_LEARNER.md → Compute).

A **synthetic** experience log sized like a year of this desk's data, so the measurement does not wait for a year:

* 250 decision sessions; about 60 optioned stocks a session with priced chains; the grid capped at 60 candidates a
  stock (``experience.cap_for``) → about 3,600 forward candidates a session, 900,000 a year, plus about 80,000 replay
  rows (20 names, weekly decision days, two years). Training reads at most ``model.MAX_ROWS`` of them;
* every feature of ``model.FEATURES`` present (a fifth missing at random, as desk scores are for ETFs and replay rows),
  the structure, bucket and sector categorical;
* returns with a planted dependence on a few features plus heavy-tailed noise, floored at −100%; label days after
  their decision (1, 5, 10, 21 sessions; expiry 7–120 days later).

The numbers it prints are **synthetic** results: they check the pipeline and the budget, not that anything trades well.
"""

from __future__ import annotations

import resource
import time
from typing import Any

import numpy as np

from desk.stocks.learner import actions as act
from desk.stocks.learner import model as md


def synthetic(rows: int = md.MAX_ROWS, sessions: int = 250, *, replay_share: float = 0.25, seed: int = 11) -> md.Dataset:
    rng = np.random.default_rng(seed)
    p = len(md.FEATURES)
    X = rng.normal(size=(rows, p)).astype(np.float32)
    X[rng.random((rows, p)) < 0.2] = np.nan
    for name, k in (("structure", len(act.STRUCTURES)), ("bucket", len(act.DTE_BUCKETS)), ("sector", 12)):
        X[:, md.FEATURES.index(name)] = rng.integers(0, k, rows)
    source = (rng.random(rows) < replay_share).astype(np.int8)
    X[:, md.FEATURES.index("source_replay")] = source
    first = 739_000
    session = np.sort(rng.integers(first, first + int(sessions * 1.45), rows)).astype(np.int32)
    col = {name: np.nan_to_num(X[:, md.FEATURES.index(name)]) for name in ("ev_risk", "iv_rv", "ret_21d")}
    signal = 0.25 * col["ev_risk"] - 0.15 * col["iv_rv"] + 0.1 * col["ret_21d"]
    structure = X[:, md.FEATURES.index("structure")]
    tail = np.where(structure < 2, rng.lognormal(-1.2, 1.1, rows), rng.normal(0.08, 0.35, rows))
    ret = np.maximum(-1.0, -0.35 + 0.3 * signal + tail - 0.25 * rng.random(rows)).astype(np.float32)
    y, day = {}, {}
    for n, t in zip((1, 5, 10, 21), ("s1", "s5", "s10", "s21"), strict=True):
        y[t] = np.maximum(-1.0, ret * min(1.0, n / 30) + rng.normal(0, 0.15, rows)).astype(np.float32)
        day[t] = (session + int(n * 1.45)).astype(np.int32)
    horizon = rng.integers(7, 120, rows)
    y["exp"], day["exp"] = ret, (session + horizon).astype(np.int32)
    for i, rule in enumerate(act.EXIT_IDS):
        y[f"exit:{rule}"] = (ret if i == 0 else np.maximum(-1.0, ret + rng.normal(0.02 * (i % 3), 0.2, rows))
                             ).astype(np.float32)
        day[f"exit:{rule}"] = day["exp"]
    missing = rng.random(rows) < 0.1
    for t in y:
        y[t][missing] = np.nan
    keys = [f"{act.STRUCTURES[int(s)].id}|d30|w0|1m" for s in structure]
    origin = ["plays:edge#1" if r < 0.02 else "grid" for r in rng.random(rows)]
    return md.Dataset(X, session, source, origin, (rng.random(rows) < 0.01).astype(np.int8), keys,
                      [f"S{i % 60}" for i in range(rows)], y, day)


def run(rows: int = md.MAX_ROWS, sessions: int = 250) -> dict[str, Any]:
    """Time and peak memory of a full training (walk-forward evaluation, final fit, importance) on synthetic data."""
    started = time.monotonic()
    ds = synthetic(rows, sessions)
    built = time.monotonic() - started
    metrics = md.evaluate(ds)
    evaluated = time.monotonic() - started
    last = metrics.pop("_last", None)
    fitted = md.fit(ds, variant=metrics.get("chosen_variant", "source"))
    blocks = md.folds(ds)
    if last is not None:
        md.importance(last, ds, np.where(ds.session >= blocks[-1][1])[0])
    total = time.monotonic() - started
    return {"synthetic": True, "rows": rows, "sessions": sessions, "features": len(md.FEATURES),
            "seconds_build": round(built, 1), "seconds_evaluate": round(evaluated - built, 1),
            "seconds_total": round(total, 1),
            "peak_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
            "models": sorted(fitted.models), "held_out": {k: metrics.get(k) for k in (
                "rank_correlation", "chosen_variant", "test_rows", "test_days", "quantile_coverage")},
            "deciles": {k: v for k, v in (metrics.get("deciles") or {}).items() if k in (
                "top_decile", "bottom_decile", "random_candidate", "top_minus_random")}}


__all__ = ["run", "synthetic"]
