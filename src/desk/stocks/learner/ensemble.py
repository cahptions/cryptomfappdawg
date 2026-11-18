"""The expected-return model's members and their ensemble (docs/STOCKS_LEARNER.md → One model or several).

Three ordinary statistical models predict the same target (the net return held to expiry at the expected fill,
clipped as in ``model``):

* **gbt**: the histogram gradient-boosted trees of ``model.PARAMS`` (missing inputs handled natively);
* **ridge**: an L2-penalised linear regression (``RIDGE_ALPHA``) on standardised inputs, missing inputs filled with
  the training median plus a missing flag, the structure, expiry bucket and sector one-hot;
* **extratrees**: extremely randomised trees (``TREES_PARAMS``), missing inputs filled with the training median, on at
  most ``MEMBER_ROWS`` training rows (a seeded sample) so it stays within a laptop's budget.

The **ensemble** is their weighted average, the weights proportional to each member's positive walk-forward rank
correlation on folds *before* the one being predicted (equal on the first). It is used only when its own walk-forward
rank correlation beats the best single member's; otherwise the best member alone is the model (``model.evaluate``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

import numpy as np
from sklearn.base import BaseEstimator, RegressorMixin

MEMBERS = ("gbt", "ridge", "extratrees")
RIDGE_ALPHA = 10.0
TREES_PARAMS = {"n_estimators": 40, "max_depth": 12, "min_samples_leaf": 40, "max_features": 0.33,
                "random_state": 7}
MEMBER_ROWS = 50_000


def content() -> dict[str, Any]:
    return {"members": list(MEMBERS), "ridge_alpha": RIDGE_ALPHA, "trees": TREES_PARAMS, "member_rows": MEMBER_ROWS,
            "ridge_inputs": "median-filled with missing flags, standardised; structure, bucket, sector one-hot",
            "weights": "proportional to each member's positive walk-forward rank correlation on earlier folds "
                       "(equal on the first fold); the final weights from every fold",
            "use": "the ensemble only when its walk-forward rank correlation beats the best member's; else that member"}


def ridge(categorical: Sequence[bool]) -> Any:
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    cats = [i for i, c in enumerate(categorical) if c]
    nums = [i for i, c in enumerate(categorical) if not c]
    prepare = ColumnTransformer([
        ("categories", Pipeline([("fill", SimpleImputer(strategy="constant", fill_value=-1.0)),
                                 ("onehot", OneHotEncoder(handle_unknown="ignore"))]), cats),
        ("numbers", Pipeline([("fill", SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)),
                              ("scale", StandardScaler())]), nums)])
    return Pipeline([("prepare", prepare), ("model", Ridge(alpha=RIDGE_ALPHA))])


def extratrees(threads: int) -> Any:
    from sklearn.ensemble import ExtraTreesRegressor
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import Pipeline

    return Pipeline([("fill", SimpleImputer(strategy="median", keep_empty_features=True)),
                     ("model", ExtraTreesRegressor(n_jobs=threads, **TREES_PARAMS))])


def fit_member(kind: str, model: Any, X: np.ndarray, y: np.ndarray, weight: np.ndarray | None, *,
               seed: int = 7) -> Any:
    """Fit a pipeline member (ridge or extratrees; the sample weight routed to its last step)."""
    if kind == "extratrees" and len(y) > MEMBER_ROWS:
        rows = np.sort(np.random.default_rng(seed).choice(len(y), MEMBER_ROWS, replace=False))
        X, y, weight = X[rows], y[rows], None if weight is None else weight[rows]
    if weight is None:
        model.fit(X, y)
    else:
        model.fit(X, y, model__sample_weight=weight)
    return model


def weights(scores: Mapping[str, float | None]) -> dict[str, float]:
    """Weights proportional to each member's positive rank correlation; equal when none is positive."""
    positive = {k: max(0.0, float(v)) for k, v in scores.items() if v is not None}
    total = sum(positive.values())
    if total <= 0:
        return {k: round(1 / len(scores), 6) for k in scores}
    return {k: round(positive.get(k, 0.0) / total, 6) for k in scores}


def combine(preds: Mapping[str, np.ndarray], judged: np.ndarray, folds: Sequence[np.ndarray],
            score: Callable[[np.ndarray, np.ndarray], float | None]) -> dict[str, Any]:
    """The walk-forward ensemble of members' held-out predictions (``folds``: each test block's rows, in time order):
    each block averaged with weights from the earlier blocks only (equal on the first); every member's and the
    ensemble's score against ``judged``; the ensemble is chosen only when it beats the best member."""
    tested = np.concatenate(list(folds)) if folds else np.zeros(0, np.int64)
    members = [m for m in preds if len(tested) and np.isfinite(preds[m][tested]).any()]

    def scored(name: str, rows: np.ndarray, values: Mapping[str, np.ndarray]) -> float | None:
        r = rows[np.isfinite(values[name][rows]) & np.isfinite(judged[rows])]
        return score(values[name][r], judged[r]) if len(r) else None

    combined = np.full(len(judged), np.nan)
    by_fold = []
    for k, rows in enumerate(folds):
        earlier = np.concatenate(list(folds[:k])) if k else np.zeros(0, np.int64)
        w = weights({m: scored(m, earlier, preds) if k else None for m in members})
        combined[rows] = sum(w[m] * np.nan_to_num(preds[m][rows]) for m in members)
        by_fold.append(w)
    everything = {**preds, "ensemble": combined}
    scores = {m: scored(m, tested, everything) for m in [*members, "ensemble"]}
    if not members:
        return {"combined": combined, "scores": scores, "weights": {}, "by_fold": by_fold, "members": [],
                "best": None, "chosen": "gbt"}
    best = max(members, key=lambda m: -2.0 if scores[m] is None else float(scores[m] or 0.0))
    together, alone = scores["ensemble"], scores[best]
    use = together is not None and alone is not None and together > alone
    return {"combined": combined, "scores": scores, "weights": weights({m: scores[m] for m in members}),
            "by_fold": by_fold, "members": members, "best": best, "chosen": "ensemble" if use else best}


class Ensemble(RegressorMixin, BaseEstimator):     # type: ignore[misc]
    """The weighted average of fitted members (each with ``predict``)."""

    def __init__(self, members: Mapping[str, Any] | None = None, weights: Mapping[str, float] | None = None) -> None:
        self.members = dict(members or {})
        self.weights = dict(weights or {})

    def fit(self, X: np.ndarray, y: np.ndarray, sample_weight: np.ndarray | None = None) -> Ensemble:
        for model in self.members.values():          # refits each member as it is (scikit-learn's estimator API)
            model.fit(X, y)
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        out = np.zeros(X.shape[0])
        for name, w in self.weights.items():
            if w > 0:
                out += w * np.asarray(self.members[name].predict(X), dtype=float)
        return out


__all__ = ["MEMBERS", "MEMBER_ROWS", "RIDGE_ALPHA", "TREES_PARAMS", "Ensemble", "combine", "content", "extratrees",
           "fit_member", "ridge", "weights"]
