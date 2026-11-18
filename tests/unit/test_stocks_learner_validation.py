"""Purged combinatorial cross-validation (desk.stocks.learner.validation): the purge and embargo hold on every split,
the paths cover every group once, and the spread of results is reported. Synthetic data only."""

import math

import numpy as np

from desk.stocks.learner import validation as va


def _rows(sessions: int = 120, per: int = 30, seed: int = 3) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    session = np.repeat(np.arange(739_000, 739_000 + sessions), per)
    label = session + rng.integers(1, 30, len(session))              # labels known 1 to 29 sessions later
    label[rng.random(len(session)) < 0.05] = 0                         # some never labelled
    return session, label, rng.normal(size=len(session))


def test_every_split_is_purged_and_embargoed() -> None:
    session, label, _ = _rows()
    splits = va.splits(session, label)
    assert len(splits) == math.comb(va.GROUPS, va.TEST_GROUPS) == 15
    bounds = va.group_bounds(session)
    unique = np.unique(session)
    embargo = max(1, math.ceil(va.EMBARGO_SHARE * len(unique)))
    for sp in splits:
        assert not (sp.train & sp.test).any()
        assert (label[sp.train] > 0).all() and (label[sp.test] > 0).all()
        for g in sp.test_groups:
            first, last = bounds[g]
            inside = (session >= first) & (session <= last)
            assert sp.test[inside].sum() == (inside & (label > 0)).sum()
            assert not (sp.train & inside).any()
            # purge: no training label reaches into a test group
            assert not (sp.train & (session < first) & (label >= first)).any()
            # embargo: nothing decided in the sessions right after a test group
            after = unique[(unique > last)][:embargo]
            assert not (sp.train & np.isin(session, after)).any()
        assert sp.train.sum() > 0
    # a training row far from every test group is kept
    kept = splits[0]
    assert kept.train[(session > bounds[4][0]) & (session < bounds[4][1]) & (label > 0)].all()


def test_the_splits_assemble_into_complete_paths() -> None:
    paths = va.paths()
    assert len(paths) == va.TEST_GROUPS * math.comb(va.GROUPS, va.TEST_GROUPS) // va.GROUPS == 5
    combos = [sp.test_groups for sp in va.splits(*_rows()[:2])]
    slots = [0] * len(combos)
    for p in paths:
        assert len(p) == va.GROUPS
        for g, i in enumerate(p):
            assert g in combos[i]                       # the split chosen for a group tests that group
            slots[i] += 1
    assert slots == [va.TEST_GROUPS] * len(combos)      # each split fills exactly as many slots as it tests groups


def test_cpcv_reports_the_spread_of_splits_and_paths() -> None:
    session, label, noise = _rows(seed=5)
    rng = np.random.default_rng(9)
    signal = rng.normal(size=len(session))
    realised = 0.3 * signal + noise
    out = va.run(session, label, realised, lambda train, test: signal[test])
    assert out["splits"] == 15 and len(out["paths"]) == 5 and out["scored"] == 15
    assert out["beat_average"] == 15 and out["rank_correlation"]["min"] > 0.1
    assert all(p["days"] > 100 and p["sharpe"] > 0 for p in out["paths"])
    blind = va.run(session, label, realised, lambda train, test: rng.normal(size=len(test)))
    assert blind["rank_correlation"]["min"] < 0.1 < out["rank_correlation"]["min"]
    assert va.run(session[:50], label[:50], realised[:50], lambda a, b: b * 0.0)["splits"] == 0
