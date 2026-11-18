"""The structure pricing in Plays and the Learner (desk.stocks.forecast.structure): every play carries its value and
the direction / volatility split, the edge ranks a section only after it ranked that section's graded plays better
(shadow otherwise), and the Learner's expiry labels carry the delta-hedged return, from stored closes and quotes
only. Synthetic worlds only."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from desk.stocks import calendar as cal
from desk.stocks import plays as pl
from desk.stocks.forecast import structure as st
from tests.stocks_world import add_chains, make_world

SYMBOLS = ["SPY", "CHEAP", "SQZ", "S10", "S11"]


@pytest.fixture()
def world(tmp_path: Path) -> Any:
    w = make_world(tmp_path, n=14)
    add_chains(w, SYMBOLS, w.days[-30:], max_days=200)
    try:
        yield w
    finally:
        w.close()


def closing(day: date) -> datetime:
    return cal.session_close(day) + timedelta(minutes=30)


def test_every_play_carries_its_value_and_an_exact_split(world: Any) -> None:
    out = pl.build(world.market, world.research, closing(world.days[-1]))
    plays = [p for c in out["categories"] for p in c["plays"]]
    assert plays and out["structure"]["ranks"] == [] and out["structure"]["fits"]["direction"] is None
    for p in plays:
        sv = p["structure_value"]
        assert sv["direction_part"] + sv["vol_part"] == pytest.approx(sv["edge"], abs=0.03)
        assert sv["fill"]["basis"] == "far side"                   # no fill observations: the ask / the bid
        assert sv["cost"] == pytest.approx((p["entry"] if p["action"] == "BUY" else -p["entry"]) * 100
                                           + 0.65 * len(p["legs"]), abs=0.02)
        assert 0 <= sv["chance_profit"] <= 1 and sv["text"] and not sv["direction_live"]
        assert sv["direction_quantiles"] is None and sv["vol_surprise"] is None
        assert "structure_ranked" not in p
    recorded = world.research.conn.execute(
        "SELECT json_extract(play_json, '$.structure_value.edge') FROM stk_plays WHERE instr(category, '@') = 0").fetchall()
    assert recorded and all(r[0] is not None for r in recorded)   # recorded in shadow with every play


def _graded(conn: Any, category: str, sessions: int, *, edge_tracks: bool, seed: int) -> None:
    rng = np.random.default_rng(seed)
    for s in range(sessions):
        session = date(2026, 1, 5).toordinal() + 7 * s
        nets = rng.normal(0, 1, 6)
        edges = nets + rng.normal(0, 0.3, 6) if edge_tracks else rng.normal(0, 1, 6)
        for rank in range(1, 7):
            play = {"structure_value": {"edge_per_risk": float(edges[rank - 1]), "chance_profit": 0.5,
                                        "driver": "volatility"}}
            conn.execute("INSERT INTO stk_plays(session, category, rank, symbol, action, structure, expiration, event, "
                         "event_kind, event_strike, event_width, entry, max_loss, legs_json, underlying, profit_kestrel, "
                         "play_json, method, recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (session, category, rank, f"S{rank}", "BUY", "long_call", session + 30, "itm", "call", 100.0,
                          0.0, 1.0, 100.0, "[]", 100.0, 0.5, json.dumps(play), "m", "2026-01-01T00:00:00+00:00"))
            conn.execute("INSERT INTO stk_play_outcomes VALUES (?,?,?,?,?,?,?,?)",
                         (session, category, rank, "2026-03-01T00:00:00+00:00", 100.0, 1, 0.0,
                          float(nets[rank - 1])))


def test_the_edge_ranks_a_section_only_after_beating_the_current_ranking(world: Any) -> None:
    conn = world.research.conn
    _graded(conn, "edge", 25, edge_tracks=True, seed=1)
    _graded(conn, "likely_itm", 25, edge_tracks=False, seed=2)
    _graded(conn, "cheap_vol", 8, edge_tracks=True, seed=3)              # too few sessions yet
    gates = st.gates(conn)
    assert gates["edge"]["live"] and gates["edge"]["edge_rho"] > 0.5
    assert not gates["likely_itm"]["live"] and not gates["cheap_vol"]["live"]
    out = pl.build(world.market, world.research, closing(world.days[-1]))
    assert out["structure"]["ranks"] == ["edge"]
    assert out["method"] == pl.method(out["promoted"], ["edge"]) != pl.method(out["promoted"])
    for c in out["categories"]:
        for p in c["plays"]:
            assert p.get("structure_ranked", False) == (c["id"] == "edge")
    cal_ = st.calibration(conn)
    assert cal_["plays"] == 6 * 58 and 0 <= cal_["brier_structure"] <= 1
