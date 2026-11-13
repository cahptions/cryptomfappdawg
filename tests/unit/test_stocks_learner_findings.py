"""The Learner's market regimes and its "do / don't" findings (desk.stocks.learner.regimes, .findings). Synthetic
datasets with planted regions only: the numbers check the method (held-out only, corrected, confirmed on later days,
retired when contradicted), never that anything trades well."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from desk.stocks.learner import actions as act
from desk.stocks.learner import bench
from desk.stocks.learner import findings as fd
from desk.stocks.learner import model as md
from desk.stocks.learner import regimes as rg
from desk.storage.db import DbKind, connect, migrate

NOW = datetime(2026, 11, 13, 22, tzinfo=UTC)


def test_regimes_follow_the_documented_rule_and_never_guess() -> None:
    calm_up = rg.of({"spy_vs_200d": 0.05, "vix": 14.0, "spy_term_slope": 0.02, "hy_change_21d": 0.0})
    assert calm_up["label"] == "calm uptrend" and calm_up["regime_vol"] == 0
    inverted = rg.codes({"spy_vs_200d": 0.05, "vix": 22.0, "spy_term_slope": -0.01, "hy_change_21d": 0.0})
    assert inverted["regime_vol"] == 2                      # elevated VIX with an inverted term structure is high
    assert rg.codes({"vix": 19.0, "spy_term_slope": -0.01})["regime_vol"] == 1
    assert rg.of({"spy_vs_200d": -0.1, "vix": 35.0, "hy_change_21d": 0.5})["label"] == \
        "high-vol downtrend, credit widening"
    unknown = rg.codes({})
    assert set(unknown.values()) == {None} and rg.label(unknown) == "unknown"
    row = md.row_vector({}, {"spy_vs_200d": 0.05, "vix": 14.0}, {}, False)
    assert row[md.FEATURES.index("regime_trend")] == 1.0 and np.isnan(row[md.FEATURES.index("regime_credit")])


def planted(rows: int = 20_000, sessions: int = 100, seed: int = 3) -> md.Dataset:
    """A synthetic log where long calls with IV rank under 20 do well and put debit spreads in high-vol markets do
    badly; regimes are set per session."""
    ds = bench.synthetic(rows, sessions, seed=seed)
    rng = np.random.default_rng(seed)
    X = ds.X
    for f, k in (("regime_trend", 2), ("regime_vol", 3), ("regime_credit", 2)):
        per = {s: rng.integers(0, k) for s in np.unique(ds.session)}
        X[:, md.FEATURES.index(f)] = [per[s] for s in ds.session]
    X[:, md.FEATURES.index("iv_rank")] = rng.uniform(0, 100, rows)
    structure = X[:, md.FEATURES.index("structure")]
    good = (structure == 0) & (X[:, md.FEATURES.index("iv_rank")] < 20)
    bad = (structure == 3) & (X[:, md.FEATURES.index("regime_vol")] == 2)
    ds.y["exp"][good] = np.maximum(-1, rng.normal(0.8, 0.3, good.sum()))
    ds.y["exp"][bad] = np.maximum(-1, rng.normal(-0.8, 0.15, bad.sum()))
    ds.index = np.arange(rows, dtype=np.int32)
    ds.key = [f"{act.STRUCTURES[int(v)].id}|d30|w0|1m" for v in structure]
    return ds


@pytest.fixture(scope="module")
def mined(tmp_path_factory: pytest.TempPathFactory) -> Any:
    mp = pytest.MonkeyPatch()
    mp.setattr(md, "PARAMS", {**md.PARAMS, "max_iter": 15})
    mp.setattr(md, "EXIT_PARAMS", {**md.EXIT_PARAMS, "max_iter": 4})
    ds = planted()
    metrics = md.evaluate(ds)
    held = metrics.pop("_heldout")
    conn = connect(Path(tmp_path_factory.mktemp("findings")) / "research.db", DbKind.STOCKS_RESEARCH)
    migrate(conn, DbKind.STOCKS_RESEARCH)
    stored = fd.save_heldout(conn, "model-a", ds, held, NOW)
    out = fd.mine(conn, ds, held, "model-a", NOW)
    yield conn, ds, held, metrics, stored, out
    conn.close()
    mp.undo()


def test_findings_come_from_held_out_rows_and_find_the_planted_regions(mined: Any) -> None:
    conn, ds, held, metrics, stored, out = mined
    assert out["looked"] and out["new"] > 0 and out["rules_tested"] >= out["kept_by_fdr"]
    tested = held["tested"]
    assert stored["rows"] == int(tested.sum()) and stored["first"] >= int(ds.session[tested].min())
    found = fd.current(conn)
    first_test = int(ds.session[tested].min())
    for f in found:
        assert f["first_session"] >= first_test                         # discovery used held-out days only
        assert f["evidence"]["days"] >= fd.MIN_DAYS and f["evidence"]["p"] <= f["evidence"]["fdr_threshold"]
        assert f["words"].startswith("Do: " if f["kind"] == "do" else "Don't: ")
        assert "held-out" in f["words"]
        if f["kind"] == "do":
            assert f["evidence"]["low"] > 0
        else:
            assert f["evidence"]["excess_high"] < 0 and f["evidence"]["mean"] < 0
    does = [f for f in found if f["kind"] == "do"]
    assert any(f["rule"]["structure"] == 0 and any(c["f"] == "iv_rank" and c.get("le") is not None
                                                     and c["le"] <= 25 for c in f["rule"]["conditions"])
               for f in does), [f["words"] for f in found]
    donts = fd.confirmed_donts(conn)
    assert any(f["rule"]["structure"] == 3 for f in donts)
    assert metrics["by_regime"] and all("regime" in r for r in metrics["by_regime"])
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE stk_learn_finding_status SET status='confirmed'")


def test_a_rule_matches_only_rows_with_the_input_present(mined: Any) -> None:
    _, ds, *_ = mined
    rule = {"structure": 0, "conditions": [{"f": "iv_rank", "gt": None, "le": 20.0}]}
    X = ds.X.copy()
    X[:, md.FEATURES.index("iv_rank")] = np.nan
    assert not fd.matches(rule, X).any()
    assert fd.describe(rule) == "Long calls with IV rank at most 20"
    assert fd.describe({"structure": 9, "conditions": [{"f": "bucket", "in": [2]},
                                                        {"f": "regime_vol", "in": [2]}]}) == \
        "Long strangles expiring about a month (22–45 days) when market volatility is high-vol"


def test_later_days_that_contradict_a_confirmed_finding_retire_it(mined: Any) -> None:
    conn, ds, held, *_ = mined
    target = next(f for f in fd.current(conn) if f["kind"] == "do" and f["status"] == "confirmed")
    later = ds.subset(np.arange(len(ds)))
    later.session = later.session + 400                            # every row after every window
    hit = fd.matches(target["rule"], later.X)
    later.y["exp"][hit] = -0.9                                      # now it loses
    again = fd.mine(conn, later, {**held, "tested": np.ones(len(later), bool)}, "model-b", NOW)
    assert again["retired"] >= 1
    status = {f["id"]: f for f in fd.current(conn)}[target["id"]]
    assert status["status"] == "retired" and [s["status"] for s in status["stages"]][-1] == "retired"
    assert {s["status"] for s in status["stages"]} >= {"candidate", "confirmed", "retired"}


def test_benjamini_hochberg_keeps_only_what_survives_the_correction() -> None:
    kept, threshold = fd.benjamini_hochberg({"a": 0.001, "b": 0.02, "c": 0.04, "d": 0.5}, q=0.10)
    assert kept == {"a", "b", "c"} and threshold == 0.04
    assert fd.benjamini_hochberg({"a": 0.2, "b": 0.5}, q=0.10) == (set(), 0.0)
