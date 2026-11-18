"""Purged combinatorial cross-validation of the outcome model (docs/STOCKS_LEARNER.md → How it is evaluated: many
held-out splits). López de Prado, *Advances in Financial Machine Learning* (2018), chapter 12.

Walk-forward gives one held-out path; a lucky or unlucky stretch of sessions decides it. **CPCV** cuts the labelled
decision sessions into ``GROUPS`` consecutive groups and tests every combination of ``TEST_GROUPS`` of them
(C(6, 2) = 15 splits), each predicted by a model trained on the other groups only:

* **purge**: a training row whose label period (its decision session to the session its label became known) overlaps a
  test group is dropped, so no training label "sees" the test sessions;
* **embargo**: training rows decided within ``EMBARGO_SHARE`` of the sessions (at least one session) after a test
  group are dropped too, as their features overlap the test group's last outcomes.

Every group is tested ``TEST_GROUPS × C(GROUPS, TEST_GROUPS) / GROUPS`` times (5), so the splits assemble into that
many complete **paths** over all the sessions, each a full out-of-sample history. Reported: per split its rank
correlation and whether each day's top tenth (by the model's expected return) made money, and beat that day's average
candidate; per path the daily top-tenth-minus-average series' mean and Sharpe ratio. The distribution, not one number,
is the result. CPCV is for model selection and reporting; promotion stays on untouched forward data
(``promotion``). At most ``MAX_ROWS`` rows (a seeded sample) and the expected-return model only, so it stays within a
laptop's budget.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from itertools import combinations
from statistics import fmean, median
from typing import Any

import numpy as np

from desk.core.hashing import canonical_json, sha256_hex

NAME = "stocks_learner_validation"
VERSION = 1
GROUPS = 6
TEST_GROUPS = 2
EMBARGO_SHARE = 0.01
MAX_ROWS = 60_000
MIN_DAY_ROWS = 10                       # a day's top tenth needs at least this many candidates


def content() -> dict[str, Any]:
    return {"version": VERSION, "method": "purged combinatorial cross-validation (Lopez de Prado 2018, ch. 12)",
            "groups": GROUPS, "test_groups": TEST_GROUPS, "embargo_share": EMBARGO_SHARE, "max_rows": MAX_ROWS,
            "purge": "drop training rows whose decision-to-label period overlaps a test group",
            "embargo": "drop training rows decided within max(1, embargo_share x sessions) sessions after a test group",
            "per_split": "rank correlation; each day's top tenth realised (profitable = mean above zero) and minus "
                         "the day's average candidate", "per_path": "mean and Sharpe of the daily top-minus-average",
            "model": "expected-return model only, the walk-forward-chosen variant and target",
            "use": "model selection and reporting; promotion stays on untouched forward data"}


def version_hash() -> str:
    return sha256_hex(canonical_json(content()))[:16]


@dataclass(frozen=True)
class Split:
    test_groups: tuple[int, ...]
    train: np.ndarray                   # rows trained on (purged and embargoed)
    test: np.ndarray                    # rows predicted


def group_bounds(sessions: np.ndarray, n_groups: int = GROUPS) -> list[tuple[int, int]]:
    """(first, last) decision session of each consecutive group of the distinct sessions."""
    unique = np.unique(sessions)
    return [(int(g[0]), int(g[-1])) for g in np.array_split(unique, n_groups) if len(g)]


def splits(sessions: np.ndarray, label_day: np.ndarray, *, n_groups: int = GROUPS, k: int = TEST_GROUPS,
           embargo_share: float = EMBARGO_SHARE) -> list[Split]:
    """Every combination of ``k`` test groups, with the purged and embargoed training rows (see the module
    docstring). ``label_day`` is the session each row's label became known (rows without a label are never used)."""
    unique = np.unique(sessions)
    bounds = group_bounds(sessions, n_groups)
    if len(bounds) < n_groups:
        return []
    embargo = max(1, math.ceil(embargo_share * len(unique)))
    labelled = label_day > 0
    out = []
    for combo in combinations(range(n_groups), k):
        test = np.zeros(len(sessions), bool)
        train = labelled.copy()
        for g in combo:
            first, last = bounds[g]
            inside = (sessions >= first) & (sessions <= last)
            test |= inside
            train &= ~inside
            train &= ~((sessions < first) & (label_day >= first))                 # purge: its label reaches in
            pos = int(np.searchsorted(unique, last, side="right"))
            stop = unique[min(pos + embargo, len(unique)) - 1] if pos < len(unique) else last
            train &= ~((sessions > last) & (sessions <= stop))                    # embargo after the group
        out.append(Split(combo, train, test & labelled))
    return out


def paths(n_groups: int = GROUPS, k: int = TEST_GROUPS) -> list[list[int]]:
    """For each path, the index of the split whose predictions fill each group (``paths()[p][g]``)."""
    combos = list(combinations(range(n_groups), k))
    per_group = [[i for i, c in enumerate(combos) if g in c] for g in range(n_groups)]
    count = len(per_group[0])
    return [[per_group[g][p] for g in range(n_groups)] for p in range(count)]


def _daily_top(sessions: np.ndarray, pred: np.ndarray, realised: np.ndarray, rows: np.ndarray
               ) -> tuple[dict[int, float], dict[int, float]]:
    """Each day's top-tenth mean realised and its excess over that day's average candidate."""
    top, excess = {}, {}
    for s in np.unique(sessions[rows]):
        r = np.where(rows & (sessions == s))[0]
        if len(r) < MIN_DAY_ROWS:
            continue
        best = r[np.argsort(pred[r])[-max(1, len(r) // 10):]]
        top[int(s)] = float(np.mean(realised[best]))
        excess[int(s)] = top[int(s)] - float(np.mean(realised[r]))
    return top, excess


def sharpe(values: Sequence[float]) -> float | None:
    """Mean over standard deviation of a series (per period, not annualised); None under 3 values or no spread."""
    if len(values) < 3:
        return None
    sd = float(np.std(values, ddof=1))
    return None if sd <= 0 else round(float(np.mean(values)) / sd, 4)


def _spread(values: Sequence[float]) -> dict[str, float] | None:
    return None if not values else {"min": round(min(values), 4), "median": round(median(values), 4),
                                    "max": round(max(values), 4)}


def run(sessions: np.ndarray, label_day: np.ndarray, realised: np.ndarray,
        fit_predict: Callable[[np.ndarray, np.ndarray], np.ndarray], *, n_groups: int = GROUPS,
        k: int = TEST_GROUPS) -> dict[str, Any]:
    """Run every split (``fit_predict(train_rows, test_rows)`` returns the test rows' predictions) and summarise the
    splits and the paths (see the module docstring)."""
    from desk.stocks.learner.model import spearman

    found = splits(sessions, label_day, n_groups=n_groups, k=k)
    if not found:
        return {"splits": 0, "reason": f"fewer than {n_groups} labelled decision sessions"}
    per_split: list[dict[str, Any]] = []
    excess_by_split: list[dict[int, float]] = []
    for sp in found:
        train, test = np.where(sp.train)[0], np.where(sp.test)[0]
        pred = np.full(len(sessions), np.nan)
        if len(train) and len(test):
            pred[test] = fit_predict(train, test)
        ok = sp.test & ~np.isnan(pred) & ~np.isnan(realised)
        top, excess = _daily_top(sessions, pred, realised, ok)
        excess_by_split.append(excess)
        per_split.append({"test_groups": list(sp.test_groups), "train_rows": int(sp.train.sum()),
                          "test_rows": int(ok.sum()), "days": len(top),
                          "rank_correlation": spearman(pred[ok], realised[ok]),
                          "top_mean": round(fmean(top.values()), 4) if top else None,
                          "top_minus_average": round(fmean(excess.values()), 4) if excess else None})
    bounds = group_bounds(sessions, n_groups)
    out_paths = []
    for p in paths(n_groups, k):
        series: dict[int, float] = {}
        for g, i in enumerate(p):
            first, last = bounds[g]
            series.update({s: v for s, v in excess_by_split[i].items() if first <= s <= last})
        values = [series[s] for s in sorted(series)]
        out_paths.append({"days": len(values), "mean": round(fmean(values), 4) if values else None,
                          "sharpe": sharpe(values)})
    scored = [s for s in per_split if s["top_mean"] is not None]
    ics = [s["rank_correlation"] for s in per_split if s["rank_correlation"] is not None]
    path_sharpes = [p["sharpe"] for p in out_paths if p["sharpe"] is not None]
    return {"splits": len(per_split), "scored": len(scored), "groups": n_groups, "test_groups": k,
            "embargo_sessions": max(1, math.ceil(EMBARGO_SHARE * len(np.unique(sessions)))),
            "profitable": sum(s["top_mean"] > 0 for s in scored),
            "beat_average": sum((s["top_minus_average"] or 0) > 0 for s in scored),
            "rank_correlation": _spread(ics), "top_mean": _spread([s["top_mean"] for s in scored]),
            "paths": out_paths, "path_sharpe": _spread(path_sharpes),
            "path_mean": _spread([p["mean"] for p in out_paths if p["mean"] is not None]), "per_split": per_split}


__all__ = ["EMBARGO_SHARE", "GROUPS", "MAX_ROWS", "NAME", "TEST_GROUPS", "VERSION", "Split", "content", "group_bounds",
           "paths", "run", "sharpe", "splits", "version_hash"]
