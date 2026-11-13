"""The outcome model: gradient-boosted trees trained walk-forward on the experience log, shown in shadow only
(docs/STOCKS_LEARNER.md → How it learns). An ordinary statistical model (scikit-learn's histogram gradient boosting);
no language model judges anything, and nothing it predicts is adopted by Plays, PAPER, Learning or any calibration.

**What it predicts, per candidate.** The chance of a profit (liquidation value above the money at risk's cost, after
fees) at 1, 5, 10 and 21 sessions and at expiry; the expected return on the money at risk if held to expiry, and its
10th, 50th and 90th percentiles; which exit rule of the grid (``actions.EXIT_RULES``) would have done best.

**Training honestly.** Rows are ordered by decision session. Walk-forward: the later ``TEST_SHARE`` of the sessions is
cut into ``FOLDS`` consecutive blocks; each block is predicted by models trained only on rows decided at least
``EMBARGO`` sessions before it **and whose label was known before it** (the purge: a 21-session or an expiry label
that resolves inside the test block is not trained on). Replay and forward rows train together; the replay flag is a
feature (variant "source feature") or replay rows are down-weighted to the forward rows' total (variant "weighted"),
both evaluated on the same held-out rows, and the better one on held-out rank correlation is the one frozen. The
returns trained on are clipped to [−100%, +``CLIP_HIGH``] (a few huge long-option wins would otherwise dominate the
squared error); every evaluation uses the real, unclipped returns.

**Compute.** At most ``MAX_ROWS`` rows (a seeded sample of whole stock-sessions when there are more), float32, fixed
tree sizes and iteration counts, one thread; ``bench`` measures the time and memory on a synthetic year.

**Frozen.** The final models (trained on every labelled row) are pickled, compressed and hashed; the registry
(``stk_learn_models``) keeps the hash, the data range, the features, the code version and the held-out results, and a
model is only ever loaded after its file's hash matches.
"""

from __future__ import annotations

import hashlib
import json
import math
import pickle
import random
import resource
import sqlite3
import statistics
import time
import zlib
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np

from desk.core.clock import to_iso
from desk.core.hashing import canonical_json, sha256_hex
from desk.stocks.learner import actions as act
from desk.stocks.learner import features as ft
from desk.stocks.learner import labels as lb
from desk.stocks.learner.experience import unpack

NAME = "stocks_learner_model"
VERSION = 1
CHECKPOINT_TARGETS = ("s1", "s5", "s10", "s21", "exp")
FEATURES = (*ft.STOCK_NAMES, *ft.MARKET_NAMES, *ft.CONTRACT_NAMES, "source_replay")
CATEGORICAL = ("structure", "bucket", "sector")
PARAMS = {"max_iter": 80, "learning_rate": 0.1, "max_leaf_nodes": 31, "min_samples_leaf": 40,
          "l2_regularization": 1.0, "early_stopping": False, "random_state": 7}
EXIT_PARAMS = {**PARAMS, "max_iter": 25}
MAX_ROWS = 150_000
THREADS = 2                         # the training subprocess's OpenMP threads (a laptop: never all of its cores)
FOLDS = 3
TEST_SHARE = 0.5
EMBARGO = 5                         # sessions between the last training decision and a test block
CLIP_HIGH = 10.0
MIN_ROWS = 300                      # labelled rows a target needs to be trained
MIN_SESSIONS = 10                   # distinct decision sessions before any training
QUANTILES = (0.1, 0.5, 0.9)
BOOTSTRAP = 1000
MODELS_DIR = "learner-models"


def content() -> dict[str, Any]:
    return {"version": VERSION, "features": list(FEATURES), "categorical": list(CATEGORICAL), "params": PARAMS,
            "exit_params": EXIT_PARAMS, "max_rows": MAX_ROWS, "folds": FOLDS, "test_share": TEST_SHARE,
            "embargo": EMBARGO, "clip_high": CLIP_HIGH, "min_rows": MIN_ROWS, "quantiles": list(QUANTILES),
            "actions": act.version_hash(), "features_version": ft.version_hash(), "labels": lb.version_hash()}


def version_hash() -> str:
    return sha256_hex(canonical_json(content()))[:16]


# ── the dataset ──────────────────────────────────────────────────────────────────────────────────
@dataclass
class Dataset:
    X: np.ndarray                                   # rows × FEATURES, float32, NaN = missing
    session: np.ndarray                             # decision session ordinal
    source: np.ndarray                              # 0 forward, 1 replay
    origin: list[str]
    explore: np.ndarray
    key: list[str]
    symbol: list[str]
    y: dict[str, np.ndarray] = field(default_factory=dict)        # target -> return (NaN = not known)
    day: dict[str, np.ndarray] = field(default_factory=dict)      # target -> the session its label became known

    def __len__(self) -> int:
        return int(self.X.shape[0])

    def subset(self, rows: np.ndarray) -> Dataset:
        return Dataset(self.X[rows], self.session[rows], self.source[rows], [self.origin[i] for i in rows],
                       self.explore[rows], [self.key[i] for i in rows], [self.symbol[i] for i in rows],
                       {k: v[rows] for k, v in self.y.items()}, {k: v[rows] for k, v in self.day.items()})


def targets() -> tuple[str, ...]:
    return (*CHECKPOINT_TARGETS, *(f"exit:{r}" for r in act.EXIT_IDS))


def row_vector(stock: Mapping[str, Any], market: Mapping[str, Any], x: Mapping[str, Any], replay: bool) -> list[float]:
    def num(v: Any) -> float:
        return float("nan") if v is None else float(v)

    return ([num(stock.get(k)) for k in ft.STOCK_NAMES] + [num(market.get(k)) for k in ft.MARKET_NAMES]
            + [num(x.get(k)) for k in ft.CONTRACT_NAMES] + [1.0 if replay else 0.0])


def _groups(conn: sqlite3.Connection, sources: Sequence[str], max_rows: int, seed: int
            ) -> list[tuple[str, int, str, int]]:
    """(source, session, symbol, candidates) of the stored groups with any label, sampled to ``max_rows`` rows."""
    rows = conn.execute(
        "SELECT c.source, c.session, c.symbol, c.candidates FROM stk_learn_candidates c WHERE c.actions_version=? AND "
        "EXISTS (SELECT 1 FROM stk_learn_labels l WHERE l.source=c.source AND l.session=c.session AND "
        "l.symbol=c.symbol AND l.actions_version=c.actions_version AND l.labels_version=?) ORDER BY c.session, c.symbol",
        (act.version_hash(), lb.version_hash())).fetchall()
    found = [(r[0], int(r[1]), r[2], int(r[3])) for r in rows if r[0] in sources]
    total = sum(g[3] for g in found)
    if total <= max_rows:
        return found
    rng = random.Random(seed)       # noqa: S311 - a reproducible sample
    rng.shuffle(found)
    kept, n = [], 0
    for g in found:
        if n + g[3] > max_rows:
            continue
        kept.append(g)
        n += g[3]
    return sorted(kept, key=lambda g: (g[1], g[2]))


def load(conn: sqlite3.Connection, *, sources: Sequence[str] = ("forward", "replay"), max_rows: int = MAX_ROWS,
         seed: int = 7) -> Dataset:
    """Every labelled candidate (at most ``max_rows``: whole stock-sessions, a seeded sample) with its targets."""
    av, lv = act.version_hash(), lb.version_hash()
    X: list[list[float]] = []
    session, source, explore = [], [], []
    origin, key, symbol = [], [], []
    names = targets()
    y: dict[str, list[float]] = {t: [] for t in names}
    day: dict[str, list[int]] = {t: [] for t in names}
    nan = float("nan")
    for src, sess, sym, _ in _groups(conn, sources, max_rows, seed):
        blob = conn.execute("SELECT rows_zlib FROM stk_learn_candidates WHERE source=? AND session=? AND symbol=? AND "
                            "actions_version=?", (src, sess, sym, av)).fetchone()[0]
        g = unpack(blob)
        labels: dict[int, dict[str, tuple[float, int]]] = defaultdict(dict)
        for checkpoint, label_day, lblob in conn.execute(
                "SELECT checkpoint, day, labels_zlib FROM stk_learn_labels WHERE source=? AND session=? AND symbol=? "
                "AND actions_version=? AND labels_version=?", (src, sess, sym, av, lv)):
            for r in lb.unpack_labels(lblob):
                if checkpoint.startswith("exp:"):
                    idx, settled, _mfe, _mae, _n, _have, exits = r
                    if settled is not None:
                        labels[idx]["exp"] = (float(settled), int(label_day))
                    for rule, value in zip(act.EXIT_IDS, exits, strict=True):
                        if value is not None:
                            labels[idx][f"exit:{rule}"] = (float(value), int(label_day))
                elif r[3] is not None:
                    labels[r[0]][checkpoint] = (float(r[3]), int(label_day))
        for idx, row in enumerate(g["rows"]):
            if not labels.get(idx):
                continue
            X.append(row_vector(g["stock"], g["market"], row["x"], src == "replay"))
            session.append(sess)
            source.append(1 if src == "replay" else 0)
            explore.append(int(row["explore"]))
            origin.append(row["origin"])
            key.append(row["key"])
            symbol.append(sym)
            for t in names:
                got = labels[idx].get(t)
                y[t].append(nan if got is None else got[0])
                day[t].append(0 if got is None else got[1])
    n = len(X)
    return Dataset(np.asarray(X, dtype=np.float32).reshape(n, len(FEATURES)), np.asarray(session, dtype=np.int32),
                   np.asarray(source, dtype=np.int8), origin, np.asarray(explore, dtype=np.int8), key, symbol,
                   {t: np.asarray(v, dtype=np.float32) for t, v in y.items()},
                   {t: np.asarray(v, dtype=np.int32) for t, v in day.items()})


# ── fitting ──────────────────────────────────────────────────────────────────────────────────────
def _categorical(columns: Sequence[str]) -> list[bool]:
    return [c in CATEGORICAL for c in columns]


def _regressor(**extra: Any) -> Any:
    from sklearn.ensemble import HistGradientBoostingRegressor

    return HistGradientBoostingRegressor(categorical_features=_categorical(FEATURES), **{**PARAMS, **extra})


def _classifier(params: Mapping[str, Any] = PARAMS) -> Any:
    from sklearn.ensemble import HistGradientBoostingClassifier

    return HistGradientBoostingClassifier(categorical_features=_categorical(FEATURES), **params)


def _known(ds: Dataset, target: str, before: int | None = None) -> np.ndarray:
    ok: np.ndarray = ~np.isnan(ds.y[target])
    if before is not None:
        ok &= ds.day[target] < before
    return ok


def best_exit(ds: Dataset) -> tuple[np.ndarray, np.ndarray]:
    """(rows whose every exit rule's return is known, the index of the best rule for each; ties → the earlier rule)."""
    returns = np.stack([ds.y[f"exit:{r}"] for r in act.EXIT_IDS], axis=1)
    ok = ~np.isnan(returns).any(axis=1)
    return ok, np.argmax(np.where(np.isnan(returns), -np.inf, returns), axis=1)


@dataclass
class Fitted:
    """The models of one fit: target -> estimator (absent when a target had too few rows)."""
    models: dict[str, Any]
    variant: str
    rows: dict[str, int]


def _weights(ds: Dataset, rows: np.ndarray, variant: str) -> np.ndarray | None:
    if variant != "weighted":
        return None
    replay = ds.source[rows] == 1
    forward = int((~replay).sum())
    if not replay.any() or not forward:
        return None
    w = np.ones(len(rows), dtype=np.float32)
    w[replay] = min(1.0, forward / int(replay.sum()))
    return w


def _matrix(ds: Dataset, rows: np.ndarray, variant: str) -> np.ndarray:
    X: np.ndarray = ds.X[rows]
    if variant == "weighted":
        X = X.copy()
        X[:, FEATURES.index("source_replay")] = np.nan          # the flag is not a feature in this variant
    return X


def fit(ds: Dataset, *, before: int | None = None, variant: str = "source", only: Sequence[str] | None = None,
        progress: Callable[[str], None] | None = None) -> Fitted:
    """Train every target (or ``only`` those) on the rows whose label was known before session ``before`` (all rows
    when None)."""
    models: dict[str, Any] = {}
    counts: dict[str, int] = {}
    say = progress or (lambda _text: None)

    def train(name: str, model: Any, rows: np.ndarray, target: np.ndarray) -> None:
        if len(rows) < MIN_ROWS or (only is not None and name not in only):
            return
        say(f"fitting {name} on {len(rows):,} rows")
        model.fit(_matrix(ds, rows, variant), target, sample_weight=_weights(ds, rows, variant))
        models[name] = model
        counts[name] = len(rows)

    hold = np.where(_known(ds, "exp", before))[0]
    clipped = np.clip(ds.y["exp"][hold], -1.0, CLIP_HIGH)
    train("expected", _regressor(), hold, clipped)
    for q in QUANTILES:
        train(f"q{round(q * 100)}", _regressor(loss="quantile", quantile=q), hold, clipped)
    for t in CHECKPOINT_TARGETS:
        rows = np.where(_known(ds, t, before))[0]
        if len(rows) and 0 < (ds.y[t][rows] > 0).mean() < 1:
            train(f"p_{t}", _classifier(), rows, (ds.y[t][rows] > 0).astype(np.int8))
    ok, best = best_exit(ds)
    rows = np.where(ok & (_known(ds, "exp", before)))[0]
    if len(np.unique(best[rows])) > 1:
        train("best_exit", _classifier(EXIT_PARAMS), rows, best[rows])
    return Fitted(models, variant, counts)


def predict(fitted: Fitted | Mapping[str, Any], X: np.ndarray, variant: str | None = None) -> dict[str, np.ndarray]:
    """Each model's prediction for the rows of ``X`` (rows × FEATURES)."""
    models = fitted.models if isinstance(fitted, Fitted) else fitted
    variant = variant or (fitted.variant if isinstance(fitted, Fitted) else "source")
    if variant == "weighted":
        X = X.copy()
        X[:, FEATURES.index("source_replay")] = np.nan
    out: dict[str, np.ndarray] = {}
    for name, model in models.items():
        if name.startswith("p_"):
            out[name] = model.predict_proba(X)[:, 1]
        elif name == "best_exit":
            proba = model.predict_proba(X)
            out["best_exit"] = model.classes_[np.argmax(proba, axis=1)]
            out["best_exit_p"] = proba.max(axis=1)
        else:
            out[name] = model.predict(X)
    return out


# ── evaluation ───────────────────────────────────────────────────────────────────────────────────
def spearman(a: np.ndarray, b: np.ndarray) -> float | None:
    if len(a) < 10:
        return None
    from scipy.stats import spearmanr

    value = float(spearmanr(a, b).statistic)
    return None if not math.isfinite(value) else round(value, 4)


def boot(by_day: Mapping[int, float], draws: int = BOOTSTRAP, seed: int = 1) -> dict[str, Any] | None:
    """Mean of daily values with a 90% bootstrap range over independent days."""
    values = list(by_day.values())
    if len(values) < 3:
        return None
    rng = random.Random(seed)       # noqa: S311 - a reproducible resample
    means = sorted(statistics.fmean(rng.choices(values, k=len(values))) for _ in range(draws))
    return {"mean": round(statistics.fmean(values), 4), "low": round(means[int(0.05 * draws)], 4),
            "high": round(means[int(0.95 * draws) - 1], 4), "days": len(values)}


def reliability(p: np.ndarray, hit: np.ndarray) -> dict[str, Any]:
    bins: dict[int, list[int]] = defaultdict(list)
    for i, x in enumerate(p):
        bins[min(int(x * 10), 9)].append(i)
    return {"rows": len(p), "brier": round(float(np.mean((p - hit) ** 2)), 4) if len(p) else None,
            "base_rate": round(float(hit.mean()), 4) if len(p) else None,
            "buckets": [{"bucket": f"{10 * b}–{10 * b + 10}%", "predicted": round(float(p[v].mean()), 4),
                         "observed": round(float(hit[v].mean()), 4), "n": len(v)} for b, v in sorted(bins.items())]}


def _daily(sessions: np.ndarray, values: np.ndarray, mask: np.ndarray) -> dict[int, float]:
    by: dict[int, list[float]] = defaultdict(list)
    for s, v, m in zip(sessions.tolist(), values.tolist(), mask.tolist(), strict=True):
        if m and math.isfinite(v):
            by[s].append(v)
    return {s: statistics.fmean(v) for s, v in by.items()}


def deciles(ds: Dataset, pred: np.ndarray, realised: np.ndarray, ok: np.ndarray) -> dict[str, Any]:
    """Each test day's top and bottom tenth of candidates by predicted return, their realised returns, the Plays picks'
    and every candidate's (a random candidate's expectation) that day; daily means, bootstrapped over days."""
    top, bottom = np.zeros(len(ds), bool), np.zeros(len(ds), bool)
    for s in np.unique(ds.session[ok]):
        rows = np.where(ok & (ds.session == s))[0]
        if len(rows) < 10:
            continue
        order = rows[np.argsort(pred[rows])]
        k = max(1, len(rows) // 10)
        bottom[order[:k]], top[order[-k:]] = True, True
    plays = np.array(["plays:" in o for o in ds.origin])
    grid = np.array([bool(k) for k in ds.key])
    explore = ds.explore == 1
    return {"top_decile": boot(_daily(ds.session, realised, ok & top)),
            "bottom_decile": boot(_daily(ds.session, realised, ok & bottom)),
            "plays_picks": boot(_daily(ds.session, realised, ok & plays)),
            "random_candidate": boot(_daily(ds.session, realised, ok & grid)),
            "explore_slice": boot(_daily(ds.session, realised, ok & explore)),
            "top_minus_random": boot({s: v - r for s, v in _daily(ds.session, realised, ok & top).items()
                                      if (r := _daily(ds.session, realised, ok & grid).get(s)) is not None}),
            "top_minus_plays": boot({s: v - r for s, v in _daily(ds.session, realised, ok & top).items()
                                     if (r := _daily(ds.session, realised, ok & plays).get(s)) is not None})}


def folds(ds: Dataset) -> list[tuple[int, int, int]]:
    """(train cut-off session, test first session, test last session) of each walk-forward block."""
    sessions = np.unique(ds.session)
    if len(sessions) < MIN_SESSIONS:
        return []
    start = int(len(sessions) * (1 - TEST_SHARE))
    blocks = np.array_split(sessions[start:], FOLDS)
    out = []
    for b in blocks:
        if not len(b):
            continue
        first = int(np.searchsorted(sessions, b[0]))
        if first - EMBARGO < 1:
            continue
        out.append((int(sessions[first - EMBARGO]), int(b[0]), int(b[-1])))
    return out


EVALUATED_WEIGHTED = ("expected",)  # the weighted variant is compared on the expected return only (compute)


def evaluate(ds: Dataset, progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Walk-forward held-out evaluation (see the module docstring). Every target is evaluated in the source-feature
    variant; the weighted variant's expected-return model is evaluated on the same rows for the choice between them.
    The last block's fitted models are returned under ``_last`` (for the permutation importance; not stored)."""
    blocks = folds(ds)
    if not blocks:
        return {"folds": 0, "reason": f"fewer than {MIN_SESSIONS} labelled decision sessions"}
    n = len(ds)
    preds: dict[str, dict[str, np.ndarray]] = {v: defaultdict(lambda: np.full(n, np.nan)) for v in ("source", "weighted")}
    tested = np.zeros(n, bool)
    last: dict[str, Fitted] = {}
    for i, (cut, first, end) in enumerate(blocks, 1):
        test = (ds.session >= first) & (ds.session <= end)
        sub = ds.subset(np.where(ds.session < cut)[0])
        for variant in ("source", "weighted"):
            if progress:
                progress(f"fold {i}/{len(blocks)} ({variant})")
            fitted = fit(sub, before=first, variant=variant, only=None if variant == "source" else EVALUATED_WEIGHTED)
            rows = np.where(test)[0]
            for name, values in predict(fitted, ds.X[rows]).items():
                preds[variant][name][rows] = values
            last[variant] = fitted
        tested |= test
    realised = ds.y["exp"]
    out: dict[str, Any] = {"folds": len(blocks), "embargo_sessions": EMBARGO,
                           "blocks": [{"train_through": date.fromordinal(c).isoformat(),
                                       "test_from": date.fromordinal(f).isoformat(),
                                       "test_to": date.fromordinal(t).isoformat()} for c, f, t in blocks],
                           "test_rows": int(tested.sum()), "test_days": len(np.unique(ds.session[tested])),
                           "test_sources": {"forward": int((tested & (ds.source == 0)).sum()),
                                            "replay": int((tested & (ds.source == 1)).sum())},
                           "variants": {}}
    for variant, p in preds.items():
        exp = p["expected"] if "expected" in p else np.full(n, np.nan)
        ok = tested & ~np.isnan(realised) & ~np.isnan(exp)
        out["variants"][variant] = {"rank_correlation": spearman(exp[ok], realised[ok]), "rows": int(ok.sum()),
                                    "by_source": {name: spearman(exp[ok & (ds.source == s)], realised[ok & (ds.source == s)])
                                                  for name, s in (("forward", 0), ("replay", 1))}}
    def score(v: str) -> float:
        return float(out["variants"][v]["rank_correlation"] if out["variants"][v]["rank_correlation"] is not None else -2)

    chosen = max(("source", "weighted"), key=score)
    out["chosen_variant"] = chosen
    out["_last"] = last.get(chosen)
    p = preds["source"]                                     # every other target: the source-feature variant
    exp = preds[chosen]["expected"] if "expected" in preds[chosen] else np.full(n, np.nan)
    ok = tested & ~np.isnan(realised) & ~np.isnan(exp)
    out["rank_correlation"] = out["variants"][chosen]["rank_correlation"]
    out["rank_correlation_days"] = len(np.unique(ds.session[ok]))
    daily_ic = {}
    for s in np.unique(ds.session[ok]):
        rows = ok & (ds.session == s)
        if rows.sum() >= 10:
            c = spearman(exp[rows], realised[rows])
            if c is not None:
                daily_ic[int(s)] = c
    out["daily_rank_correlation"] = boot(daily_ic)
    out["deciles"] = deciles(ds, exp, realised, ok)
    out["calibration"] = {}
    for t in CHECKPOINT_TARGETS:
        name = f"p_{t}"
        if name not in p:
            continue
        rows = tested & ~np.isnan(ds.y[t]) & ~np.isnan(p[name])
        out["calibration"][t] = reliability(p[name][rows], (ds.y[t][rows] > 0).astype(float))
        out["calibration"][t]["days"] = len(np.unique(ds.session[rows]))
    q10, q90 = p.get("q10"), p.get("q90")
    if q10 is not None and q90 is not None:
        rows = ok & ~np.isnan(q10) & ~np.isnan(q90)
        inside = (realised[rows] >= q10[rows]) & (realised[rows] <= q90[rows])
        out["quantile_coverage"] = {"rows": int(rows.sum()), "inside_10_90": round(float(inside.mean()), 4)
                                    if rows.any() else None, "expected": 0.8}
    if "best_exit" in p:
        okx, best = best_exit(ds)
        rows = np.where(tested & okx & ~np.isnan(p["best_exit"]))[0]
        if len(rows):
            chosen_rule = p["best_exit"][rows].astype(int)
            returns = np.stack([ds.y[f"exit:{r}"] for r in act.EXIT_IDS], axis=1)[rows]
            realised_rule = returns[np.arange(len(rows)), chosen_rule]
            hold = returns[:, 0]
            out["best_exit"] = {"rows": len(rows), "accuracy": round(float((chosen_rule == best[rows]).mean()), 4),
                                "chosen_mean": round(float(realised_rule.mean()), 4),
                                "hold_mean": round(float(hold.mean()), 4),
                                "versus_hold": boot(_daily(ds.session[rows], realised_rule - hold,
                                                           np.ones(len(rows), bool)))}
    return out


def importance(fitted: Fitted, ds: Dataset, rows: np.ndarray, *, limit: int = 4000, top: int = 10) -> list[dict[str, Any]]:
    """Permutation importance of the expected-return model on held-out rows, in plain words."""
    from sklearn.inspection import permutation_importance

    model = fitted.models.get("expected")
    ok = rows[~np.isnan(ds.y["exp"][rows])]
    if model is None or len(ok) < 200:
        return []
    rng = np.random.default_rng(3)
    ok = rng.choice(ok, size=min(limit, len(ok)), replace=False)
    result = permutation_importance(model, _matrix(ds, ok, fitted.variant), ds.y["exp"][ok], n_repeats=3,
                                    random_state=3, scoring="r2", n_jobs=1)
    about = {f.name: f.about for f in ft.ALL} | {"source_replay": "Whether the row came from the replay"}
    order = np.argsort(-result.importances_mean)[:top]
    return [{"feature": FEATURES[i], "about": about.get(FEATURES[i], FEATURES[i]),
             "importance": round(float(result.importances_mean[i]), 5),
             "spread": round(float(result.importances_std[i]), 5)} for i in order if result.importances_mean[i] > 0]


# ── the registry ─────────────────────────────────────────────────────────────────────────────────
def _peak_mb() -> float:
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


def data_summary(ds: Dataset) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, s in (("forward", 0), ("replay", 1)):
        rows = ds.source == s
        if not rows.any():
            continue
        sessions = ds.session[rows]
        out[name] = {"rows": int(rows.sum()), "sessions": len(np.unique(sessions)),
                     "first": date.fromordinal(int(sessions.min())).isoformat(),
                     "last": date.fromordinal(int(sessions.max())).isoformat(),
                     "labelled": {t: int((~np.isnan(ds.y[t][rows])).sum()) for t in CHECKPOINT_TARGETS}}
    return out


def train(conn: sqlite3.Connection, home: Path, now: datetime, *, max_rows: int = MAX_ROWS,
          progress: Callable[[str], None] | None = None, note: str | None = None) -> dict[str, Any]:
    """Load, evaluate walk-forward, fit the final models on every labelled row, freeze and register them."""
    started = time.monotonic()
    say = progress or (lambda _text: None)
    say("loading the experience log")
    ds = load(conn, max_rows=max_rows)
    if len(ds) < MIN_ROWS or len(np.unique(ds.session)) < MIN_SESSIONS:
        return {"trained": False, "rows": len(ds), "sessions": len(np.unique(ds.session)),
                "reason": f"needs {MIN_ROWS} labelled candidates from {MIN_SESSIONS} sessions"}
    metrics = evaluate(ds, progress)
    variant = metrics.get("chosen_variant", "source")
    last = metrics.pop("_last", None)
    say("fitting the final models")
    fitted = fit(ds, variant=variant)
    if not fitted.models:
        return {"trained": False, "rows": len(ds), "reason": "no target had enough labelled rows"}
    blocks = folds(ds)
    if blocks and last is not None:
        metrics["importance"] = importance(last, ds, np.where(ds.session >= blocks[-1][1])[0])
    metrics["targets_rows"] = fitted.rows
    blob = zlib.compress(pickle.dumps({"models": fitted.models, "variant": variant, "features": list(FEATURES),
                                       "code": version_hash()}, protocol=5), 6)
    digest = hashlib.sha256(blob).hexdigest()
    ident = digest[:16]
    folder = home / MODELS_DIR
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / f"{ident}.pkl.z"
    if not path.exists():
        path.write_bytes(blob)
        path.chmod(0o600)
    seconds = round(time.monotonic() - started, 1)
    conn.execute("INSERT OR IGNORE INTO stk_learn_models(id, trained_at, code_version, data_json, features_json, "
                 "metrics_json, artifact, artifact_sha256, bytes, seconds, peak_mb, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                 (ident, to_iso(now), version_hash(), json.dumps(data_summary(ds)), json.dumps(list(FEATURES)),
                  json.dumps(metrics, allow_nan=False), path.name, digest, len(blob), seconds, _peak_mb(), note))
    return {"trained": True, "id": ident, "rows": len(ds), "seconds": seconds, "peak_mb": _peak_mb(),
            "bytes": len(blob), "variant": variant}


def latest(conn: sqlite3.Connection) -> sqlite3.Row | None:
    try:
        row: sqlite3.Row | None = conn.execute("SELECT * FROM stk_learn_models WHERE code_version=? "
                                               "ORDER BY trained_at DESC LIMIT 1", (version_hash(),)).fetchone()
        return row
    except sqlite3.OperationalError:
        return None


def load_models(home: Path, row: Mapping[str, Any]) -> Fitted | None:
    """The frozen models of a registry row, only when the file's hash matches the registry."""
    path = home / MODELS_DIR / str(row["artifact"])
    try:
        blob = path.read_bytes()
    except OSError:
        return None
    if hashlib.sha256(blob).hexdigest() != row["artifact_sha256"]:
        return None
    value = pickle.loads(zlib.decompress(blob))  # noqa: S301 - our own artifact, written here and verified by its hash
    if value.get("features") != list(FEATURES):
        return None
    return Fitted(value["models"], value["variant"], {})


def describe(p: Mapping[str, np.ndarray], i: int) -> dict[str, Any]:
    """One row's predictions as shown (shadow)."""
    def val(name: str) -> float | None:
        v = p.get(name)
        return None if v is None or not math.isfinite(float(v[i])) else round(float(v[i]), 4)

    out: dict[str, Any] = {"p_profit": {t: val(f"p_{t}") for t in CHECKPOINT_TARGETS if f"p_{t}" in p},
                           "expected": val("expected"), "q10": val("q10"), "q50": val("q50"), "q90": val("q90")}
    if "best_exit" in p:
        rule = act.EXIT_RULES[int(p["best_exit"][i])]
        out["best_exit"] = {"id": rule.id, "title": rule.title, "p": val("best_exit_p")}
    return out


__all__ = ["CHECKPOINT_TARGETS", "FEATURES", "MAX_ROWS", "NAME", "VERSION", "Dataset", "Fitted", "best_exit", "boot",
           "content", "data_summary", "describe", "evaluate", "fit", "folds", "importance", "latest", "load",
           "load_models", "predict", "reliability", "row_vector", "spearman", "train", "version_hash"]
