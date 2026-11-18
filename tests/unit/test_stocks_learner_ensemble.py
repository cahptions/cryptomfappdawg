"""The expected-return members and their ensemble (desk.stocks.learner.ensemble): weights from earlier folds only, the
ensemble used only when it beats the best single member held out, and members that cope with missing inputs.
Synthetic data only."""

import pickle

import numpy as np
from scipy.stats import spearmanr

from desk.stocks.learner import bench
from desk.stocks.learner import ensemble as en
from desk.stocks.learner import model as md


def _ic(a: np.ndarray, b: np.ndarray) -> float | None:
    return None if len(a) < 10 else float(spearmanr(a, b).statistic)


def _folds(n: int, k: int = 3) -> list[np.ndarray]:
    return list(np.array_split(np.arange(n), k))


def test_the_ensemble_is_used_only_when_it_beats_the_best_member() -> None:
    rng = np.random.default_rng(1)
    n = 9000
    a, b = rng.normal(size=n), rng.normal(size=n)
    judged = a + b + rng.normal(0, 1.0, n)
    halves = {"gbt": a + rng.normal(0, 0.3, n), "ridge": b + rng.normal(0, 0.3, n), "extratrees": rng.normal(size=n)}
    out = en.combine(halves, judged, _folds(n), _ic)
    assert out["chosen"] == "ensemble" and out["scores"]["ensemble"] > max(out["scores"][m] for m in halves)
    assert out["by_fold"][0] == {"gbt": round(1 / 3, 6), "ridge": round(1 / 3, 6), "extratrees": round(1 / 3, 6)}
    assert out["weights"]["extratrees"] < 0.05 < out["weights"]["gbt"]
    # one strong member, the others worse than useless: the ensemble adds nothing, so the best member is used alone
    strong = {"gbt": judged + rng.normal(0, 0.5, n), "ridge": -judged + rng.normal(0, 2, n),
              "extratrees": -judged + rng.normal(0, 3, n)}
    alone = en.combine(strong, judged, _folds(n), _ic)
    assert alone["chosen"] == "gbt" and alone["best"] == "gbt"
    assert alone["weights"] == {"gbt": 1.0, "ridge": 0.0, "extratrees": 0.0}


def test_weights_use_only_earlier_folds() -> None:
    rng = np.random.default_rng(2)
    n = 6000
    judged = rng.normal(size=n)
    preds = {"gbt": judged + rng.normal(0, 1, n), "ridge": judged + rng.normal(0, 3, n)}
    folds = _folds(n)
    before = en.combine(preds, judged, folds, _ic)
    changed = {k: v.copy() for k, v in preds.items()}
    changed["ridge"][folds[2]] = judged[folds[2]]                   # the last block suddenly perfect for ridge
    after = en.combine(changed, judged, folds, _ic)
    assert after["by_fold"][:3] == before["by_fold"][:3]            # no fold's weights saw its own or a later block
    assert np.array_equal(after["combined"][folds[0]], before["combined"][folds[0]])
    assert np.array_equal(after["combined"][folds[1]], before["combined"][folds[1]])


def test_members_fit_with_missing_inputs_and_the_ensemble_pickles() -> None:
    ds = bench.synthetic(3000, 30)
    rows = np.arange(2000)
    members = {kind: md.fit_member(kind, ds.subset(rows), None, "source", md.TARGET) for kind in en.MEMBERS}
    assert all(m is not None for m in members.values())
    X = ds.X[2000:]
    assert np.isnan(X).any()
    for model in members.values():
        assert np.isfinite(model.predict(X)).all()
    ens = en.Ensemble(members, {"gbt": 0.5, "ridge": 0.3, "extratrees": 0.2})
    want = sum(w * members[k].predict(X) for k, w in ens.weights.items())
    assert np.allclose(ens.predict(X), want)
    assert np.allclose(pickle.loads(pickle.dumps(ens)).predict(X), want)
    assert md.predict(md.Fitted({"expected": ens}, "source", {}), X)["expected"].shape == (len(X),)
