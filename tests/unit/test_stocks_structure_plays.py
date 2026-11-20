"""The structure pricing in Plays and the Learner (desk.stocks.forecast.structure): every play carries its value and
the direction / volatility split, the edge ranks a section only after it ranked that section's graded plays better
(shadow otherwise), and the Learner's expiry labels carry the delta-hedged return, from stored closes and quotes
only. Synthetic worlds only."""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from desk.stocks import calendar as cal
from desk.stocks import plays as pl
from desk.stocks.forecast import structure as st
from desk.stocks.learner import experience as ex
from desk.stocks.learner import features as ft
from desk.stocks.learner import labels as lb
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


def test_learner_features_read_the_forecast_block(world: Any) -> None:
    session = world.days[-30]
    ex.record_forward(world.market, world.research, session, closing(session))
    g = ex.unpack(world.research.conn.execute("SELECT rows_zlib FROM stk_learn_candidates LIMIT 1").fetchone()[0])
    x = g["rows"][0]["x"]
    for name in ("dir_q10", "dir_q50", "dir_q90", "vol_surprise", "struct_edge_risk", "struct_dir_share",
                 "struct_chance"):
        assert name in ft.CONTRACT_NAMES and name in x
    assert x["struct_edge_risk"] is not None and x["dir_q50"] is None    # no direction fit yet: missing, not zero
    assert ft.VERSION == 4


# ── the delta-hedged label ───────────────────────────────────────────────────────────────────────
def _call(expiration: date) -> lb.Candidate:
    return lb.Candidate(0, "BUY", 5.0, 565.0, expiration.toordinal(),
                        [{"occ": "C100", "kind": "call", "strike": 100.0, "side": "buy"}])


def test_delta_hedged_arithmetic() -> None:
    days = cal.trading_days(date(2026, 3, 2), date(2026, 3, 6))[:3]
    expiration = date(2026, 3, 20)
    c = _call(expiration)
    decision = {"C100": (4.9, 5.1)}
    path = {days[1].toordinal(): {"C100": (6.9, 7.1)}, days[2].toordinal(): {"C100": (5.4, 5.6)}}
    closes = [100.0, 103.0, 101.0]
    got = lb.delta_hedged(c, decision, path, days, closes, 104.0, 0.04)
    d0 = lb.position_delta(c.legs, decision, 100.0, days[0], c.expiration, 0.04)
    d1 = lb.position_delta(c.legs, path[days[1].toordinal()], 103.0, days[1], c.expiration, 0.04)
    d2 = lb.position_delta(c.legs, path[days[2].toordinal()], 101.0, days[2], c.expiration, 0.04)
    assert d0 is not None and d1 is not None and d2 is not None and 0.4 < d0 < 0.7
    hedge = -(d0 * 3.0 + d1 * -2.0 + d2 * 3.0)
    assert got == pytest.approx((4.0 - 5.0 + hedge) / 5.65)
    # a session without the quote keeps the previous delta
    gap = {days[2].toordinal(): path[days[2].toordinal()]}
    got2 = lb.delta_hedged(c, decision, gap, days, closes, 104.0, 0.04)
    assert got2 == pytest.approx((4.0 - 5.0 - (d0 * 3.0 + d0 * -2.0 + d2 * 3.0)) / 5.65)
    # no stored close, or no decision quote: missing, never estimated
    assert lb.delta_hedged(c, decision, path, days, [100.0, None, 101.0], 104.0, 0.04) is None
    assert lb.delta_hedged(c, {}, path, days, closes, 104.0, 0.04) is None
    assert lb.delta_hedged(c, decision, path, days, closes, None, 0.04) is None


def test_a_sold_position_hedges_the_other_way() -> None:
    days = cal.trading_days(date(2026, 3, 2), date(2026, 3, 3))
    put = lb.Candidate(0, "SELL", 2.0, 300.0, date(2026, 3, 20).toordinal(),
                       [{"occ": "P95", "kind": "put", "strike": 95.0, "side": "sell"}])
    decision = {"P95": (1.9, 2.1)}
    d0 = lb.position_delta(put.legs, decision, 100.0, days[0], put.expiration, 0.04)
    assert d0 is not None and d0 > 0                                  # short a put: long delta
    got = lb.delta_hedged(put, decision, {}, days, [100.0, 98.0], 97.0, 0.04)
    assert got == pytest.approx((0.0 + 2.0 - d0 * (98.0 - 100.0) - d0 * (97.0 - 98.0)) / 3.0)


def test_expiry_labels_carry_the_delta_hedged_return(world: Any) -> None:
    session = world.days[-30]
    ex.record_forward(world.market, world.research, session, closing(session))
    now = cal.session_close(world.days[-1]) + timedelta(hours=4)
    lb.run(world.research.conn, world.market.conn, lb.ForwardPrices(world.market.conn, world.research.conn), now)
    rows = [x for (blob,) in world.research.conn.execute(
        "SELECT labels_zlib FROM stk_learn_labels WHERE checkpoint LIKE 'exp:%'") for x in lb.unpack_labels(blob)]
    assert rows and all(len(x) == 8 for x in rows)
    hedged = [x[7] for x in rows if x[7] is not None]
    assert hedged and all(math.isfinite(v) for v in hedged)
    assert lb.VERSION == 2


def test_the_model_version_moves_with_the_new_features_and_labels() -> None:
    from desk.stocks.learner import model as md

    assert md.VERSION == 9 and "dir_q50" in md.FEATURES and "struct_edge_risk" in md.FEATURES


def test_the_direction_vs_volatility_insight_reads_the_stored_fits(world: Any) -> None:
    from desk.stocks.forecast import direction as dr
    from desk.stocks.forecast import insight, store
    from desk.stocks.forecast import vol_surprise as vs
    from desk.stocks.learner.account import forecast_view

    assert insight.summary(world.market.conn, world.research.conn) is None
    assert forecast_view(world.market.conn, world.research.conn) is None
    e = {"rows": 9000, "sessions": 300, "stocks": 400, "pinball": 0.21, "pinball_baseline": 0.22, "t": 3.1,
         "calibration_error": 0.006, "calibration_error_baseline": 0.005, "neutral_ic": 0.03, "live": True}
    off = {**e, "pinball": 0.23, "t": -1.0, "live": False}
    now = closing(world.days[-1])
    store.save(world.market.conn, dr.MODEL, dr.VERSION, world.days[-1], {}, {1: True, 5: False},
               {"1": e, "5": off}, 150000, 1200, 123.0, 468.0, now)
    store.save(world.market.conn, vs.MODEL, vs.VERSION, world.days[-1], {}, {1: False},
               {"1": {"rows": 3000, "sessions": 90, "mse": 0.31, "mse_har": 0.30, "skill": -0.03, "t": -0.5,
                      "live": False}}, 5000, 300, 20.0, None, now)
    info = insight.summary(world.market.conn, world.research.conn)
    assert info is not None and info["direction"]["live"] == [1] and info["vol_surprise"]["live"] == []
    headline, detail = insight.sentences(info)
    assert headline == "Direction vs volatility: direction live at 1 of 5 horizons, volatility from HAR-IV alone."
    assert "1 session: pinball 0.210 vs 0.220" in detail and "not beating HAR-IV alone yet" in detail
    assert "no section's ranking uses it yet" in detail
    view = forecast_view(world.market.conn, world.research.conn)
    assert view is not None and view["headline"] == headline
