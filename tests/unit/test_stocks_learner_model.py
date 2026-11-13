"""The Learner's outcome model (desk.stocks.learner.model): walk-forward with purge and embargo, the frozen artifact
and its registry, and the shadow views. Synthetic world and synthetic datasets only: the numbers here check the
pipeline, never that anything trades well."""

import json
import sqlite3
from datetime import timedelta
from typing import Any

import numpy as np
import pytest

from desk.stocks import calendar as cal
from desk.stocks import plays as pl
from desk.stocks.learner import bench, view
from desk.stocks.learner import experience as ex
from desk.stocks.learner import labels as lb
from desk.stocks.learner import model as md
from tests.stocks_world import add_chains, make_world

SYMBOLS = ["SPY", "CHEAP", "SQZ", "S10", "S11", "S12"]


@pytest.fixture(scope="module")
def learned(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """Forty recorded closing sessions on six stocks, labelled through the last stored day."""
    w = make_world(tmp_path_factory.mktemp("learner"), n=14)
    add_chains(w, SYMBOLS, w.days[-70:], max_days=120)
    for d in w.days[-70:-28]:
        close = cal.session_close(d) + timedelta(minutes=30)
        pl.build(w.market, w.research, close)
        ex.record_forward(w.market, w.research, d, close)
    now = cal.session_close(w.days[-1]) + timedelta(hours=4)
    lb.run(w.research.conn, w.market.conn, lb.ForwardPrices(w.market.conn, w.research.conn), now, limit=10_000)
    try:
        yield w, now
    finally:
        w.close()


@pytest.fixture()
def quick(monkeypatch: Any) -> None:
    monkeypatch.setattr(md, "PARAMS", {**md.PARAMS, "max_iter": 8})
    monkeypatch.setattr(md, "EXIT_PARAMS", {**md.EXIT_PARAMS, "max_iter": 4})


def test_the_dataset_has_every_feature_and_only_known_labels(learned: Any) -> None:
    w, _ = learned
    ds = md.load(w.research.conn)
    assert len(ds) > 1000 and ds.X.shape[1] == len(md.FEATURES)
    assert set(np.unique(ds.source)) == {0}
    for t in md.targets():
        known = ~np.isnan(ds.y[t])
        assert (ds.day[t][known] > ds.session[known]).all()          # a label is always after its decision
        assert (ds.y[t][known] >= -1.0 - 1e-6).all()
    assert (~np.isnan(ds.y["s1"])).sum() > (~np.isnan(ds.y["exp"])).sum() > 0
    capped = md.load(w.research.conn, max_rows=500)
    assert 0 < len(capped) <= 500


def test_walk_forward_never_trains_on_a_label_known_after_the_test_starts() -> None:
    ds = bench.synthetic(6000, 60)
    blocks = md.folds(ds)
    assert len(blocks) == md.FOLDS
    sessions = np.unique(ds.session)
    for cut, first, last in blocks:
        assert first <= last and cut < first
        assert np.searchsorted(sessions, first) - np.searchsorted(sessions, cut) == md.EMBARGO
        train = ds.session < cut
        for t in md.targets():
            used = train & md._known(ds, t, first)
            assert (ds.day[t][used] < first).all()
    later = md._known(ds, "exp", int(sessions[30]))
    assert not (ds.day["exp"][later] >= sessions[30]).any()


def test_training_freezes_registers_and_refuses_a_tampered_artifact(learned: Any, quick: None) -> None:
    w, now = learned
    out = md.train(w.research.conn, w.paths.home, now)
    assert out["trained"], out
    row = md.latest(w.research.conn)
    assert row is not None and row["id"] == out["id"] and row["code_version"] == md.version_hash()
    metrics = json.loads(row["metrics_json"])
    assert metrics["folds"] >= 1 and metrics["test_days"] > 0
    assert metrics["chosen_variant"] in ("source", "weighted")
    assert set(json.loads(row["data_json"])) == {"forward"}
    assert json.loads(row["features_json"]) == list(md.FEATURES)
    fitted = md.load_models(w.paths.home, row)
    assert fitted is not None and "expected" in fitted.models
    path = w.paths.home / md.MODELS_DIR / row["artifact"]
    good = path.read_bytes()
    path.write_bytes(good[:-1] + bytes([good[-1] ^ 1]))
    assert md.load_models(w.paths.home, row) is None                    # its hash no longer matches: never loaded
    path.write_bytes(good)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        w.research.conn.execute("UPDATE stk_learn_models SET note='x'")


def test_the_shadow_views_change_nothing_the_rules_decide(learned: Any, quick: None) -> None:
    w, now = learned
    if md.latest(w.research.conn) is None:
        md.train(w.research.conn, w.paths.home, now)
    board = pl.build(w.market, w.research, now)
    from desk.stocks.connection import _atomic_private

    _atomic_private(w.paths.home / "stocks-plays.json", json.dumps(board, allow_nan=False))
    plays_before = w.research.conn.execute("SELECT COUNT(*), MAX(recorded_at) FROM stk_plays").fetchone()[:]
    out = view.refresh(w.paths, w.dbs, now, force=True)
    assert out["refreshed"]
    saved = view.saved(w.paths.home)
    assert saved is not None and saved["label"].startswith("Shadow")
    assert saved["model"]["id"] == md.latest(w.research.conn)["id"]
    shown = [p for c in board["categories"] for p in c["plays"]]
    if shown:
        assert saved["plays"]
        one = next(iter(saved["plays"].values()))
        assert set(one) >= {"p_profit", "expected", "q10", "q90"}
        for v in saved["plays"].values():
            assert all(0 <= p <= 1 for p in v["p_profit"].values() if p is not None)
    ideas = saved["ideas"]["items"]
    assert ideas and all(i["learner"]["expected"] is not None for i in ideas)
    assert [i["learner"]["expected"] for i in ideas] == sorted((i["learner"]["expected"] for i in ideas), reverse=True)
    assert w.research.conn.execute("SELECT COUNT(*), MAX(recorded_at) FROM stk_plays").fetchone()[:] == plays_before
    assert pl.build(w.market, w.research, now)["categories"] == board["categories"]   # the board itself is unchanged
    assert view.refresh(w.paths, w.dbs, now)["refreshed"] is False                     # throttled


def test_the_synthetic_benchmark_runs_end_to_end(quick: None) -> None:
    out = bench.run(rows=4000, sessions=40)
    assert out["synthetic"] is True and "expected" in out["models"] and out["seconds_total"] > 0


def test_the_learning_report_has_a_learner_section(learned: Any, quick: None) -> None:
    from desk.stocks import learning_report as lr
    from desk.stocks.accuracy import report_problems

    w, now = learned
    if md.latest(w.research.conn) is None:
        md.train(w.research.conn, w.paths.home, now)
    report = lr.build(w.paths, now)
    section = next(s for s in report["sections"] if s["id"] == "learner")
    headlines = " ".join(i["headline"] for i in section["items"])
    assert "recorded" in headlines and "trained" in headlines
    assert all(i["strength"] in ("early", "suggestive") for i in section["items"])    # never "tested": shadow only
    assert not report_problems(report)
    assert "The Learner" in lr.render_markdown(report)
