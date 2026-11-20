"""Read-only held-out feature-block comparisons on the same stored candidate rows."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from statistics import fmean
from typing import Any

import numpy as np

from desk.stocks.learner import features, model, regimes


def _score(ds: model.Dataset, predicted: np.ndarray) -> dict[str, Any]:
    realized = ds.y[model.TARGET]
    good = np.isfinite(realized) & np.isfinite(predicted)
    lifts, ics = [], []
    for day in np.unique(ds.session[good]):
        ix = np.flatnonzero(good & (ds.session == day))
        if len(ix) < 10:
            continue
        top = ix[np.argsort(predicted[ix], kind="stable")[-max(1, len(ix) // 10):]]
        lifts.append(float(realized[top].mean() - realized[ix].mean()))
        corr = model.spearman(predicted[ix], realized[ix])
        if corr is not None:
            ics.append(corr)
    return {"rows": int(good.sum()), "days": len(lifts), "rank_ic": round(fmean(ics), 5) if ics else None,
            "top_decile_minus_eligible_mean_pp": round(fmean(lifts) * 100, 3) if lifts else None}


def run(research: sqlite3.Connection, market: sqlite3.Connection, now: datetime, *,
        rows: int = 20_000, source: str = "forward") -> dict[str, Any]:
    ds = model.load(research, sources=(source,), max_rows=rows, market=market, available_at=now)
    blocks = model.folds(ds)
    result: dict[str, Any] = {"source": source, "decision_rows": len(ds), "folds": len(blocks), "variants": {},
                              "basis": "Purged forward-in-time fit on identical stored candidates. Descriptive comparison."}
    if not blocks:
        return {**result, "reason": "Too few recorded decisions for a walk-forward comparison."}
    all_names = set(model.FEATURES)
    forecasts = {f.name for f in features.FORECAST}
    contracts = set(features.CONTRACT_NAMES) - forecasts
    variants = {
        "full_tree": set(), "no_direction_volatility_or_structure_forecasts": forecasts,
        "no_options_surface": {n for n in features.STOCK_NAMES if n.startswith(("iv", "opt_", "term_slope", "skew"))},
        "no_stock_technicals": {n for n in features.STOCK_NAMES if n.startswith(("ret_", "above_", "dist_", "rv", "dollar"))},
        "no_earnings": {n for n in features.STOCK_NAMES if n.startswith("earn")},
        "no_market_or_regime": set(features.MARKET_NAMES) | set(regimes.FEATURES),
        "no_desks": {n for n in features.STOCK_NAMES if n.startswith(("desk_", "committee"))},
        "no_contract_or_odds": contracts, "contract_only_tree": all_names - contracts, "full_ridge": set(),
    }
    original = ds.X.copy()
    predictions = {"seeded_random": np.random.default_rng(47).random(len(ds))}
    for name, masked in variants.items():
        ds.X[:] = original
        for feature in masked:
            ds.X[:, model.FEATURES.index(feature)] = np.nan
        pred = np.full(len(ds), np.nan)
        for cut, first, last in blocks:
            fit = ds.subset(np.flatnonzero(ds.session < cut))
            test = np.flatnonzero((ds.session >= first) & (ds.session <= last))
            regressor = model.fit_member("ridge" if name == "full_ridge" else "gbt", fit, first, "source", model.TARGET)
            if regressor is not None:
                out = model.predict(model.Fitted({"expected": regressor}, "source", {}), ds.X[test])
                pred[test] = out["expected"]
        predictions[name] = pred
    common = np.logical_and.reduce([np.isfinite(pred) for pred in predictions.values()])
    result["variants"] = {name: _score(ds, np.where(common, pred, np.nan)) for name, pred in predictions.items()}
    if not result["variants"]["full_tree"]["days"]:
        result["reason"] = "Too few labels available at the fold cutoffs to compare ranks on common days."
    return result
