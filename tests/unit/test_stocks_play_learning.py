"""Plays learning: exit rules chosen walk-forward, challengers promoted only on later-session evidence with a block
bootstrap and Holm correction, failing and proven labels, calibration per category and days to expiry, and which
inputs predict returns."""

from __future__ import annotations

import json
import random
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from desk.core.paths import DeskPaths
from desk.stocks import calendar as cal
from desk.stocks import odds as od
from desk.stocks import play_learning as lrn
from desk.stocks import plays as pl
from desk.stocks.play_performance import PlayReturn
from desk.stocks.repo import StocksDatabases, StocksResearch, day_of
from tests.conftest import REPO

START = date(2025, 1, 2)
SESSIONS = cal.trading_days(START, START + timedelta(days=200))
NOW = datetime(2025, 12, 1, 22, tzinfo=UTC)


def play(session: date, path: list[tuple[int, float, str]], category: str = "edge", twin: list[Any] | None = None,
         features: dict[str, Any] | None = None, expiration: date | None = None) -> PlayReturn:
    exp = expiration or cal.trading_days(session, session + timedelta(days=60))[max(i for i, _, _ in path)]
    return PlayReturn(session, category, 1, "m", "AAL", "BUY", "long_call", exp, 1.0, (exp - session).days,
                      features or {}, path=path, twin=twin or [])


# ── exit rules ───────────────────────────────────────────────────────────────────────────────────
def test_exits_act_on_quoted_closes_only_and_fall_back_to_settlement() -> None:
    path = [(0, -0.05, "quoted"), (1, 0.3, "estimated"), (2, 0.6, "quoted"), (3, -0.4, "quoted"),
            (4, -1.0, "settled")]
    assert lrn.exit_return(path, (0.5, None, None)) == (0.6, 2)          # +30% was an estimate: no exit there
    assert lrn.exit_return(path, (0.25, None, None)) == (0.6, 2)
    assert lrn.exit_return(path, (None, 0.25, None)) == (-0.4, 3)
    assert lrn.exit_return(path, (None, None, 3)) == (-0.4, 3)
    assert lrn.exit_return(path, (None, None, None)) == (-1.0, 4)         # hold to expiry
    assert lrn.exit_return(path[:3], (None, None, None)) is None          # still open: unknown
    assert lrn.rules()[0] == (None, None, None) and len(lrn.rules()) == 80
    assert lrn.describe((0.5, 0.25, 10)) == "take profit at +50%, stop at −25%, leave after 10 sessions"


def test_walk_forward_finds_a_real_exit_edge_without_looking_ahead() -> None:
    rng = random.Random(1)
    rows = []
    for s in SESSIONS[:80]:
        for _ in range(4):        # every play spikes to +60% then usually expires worthless: taking profit wins
            spike = rng.random() < 0.8
            path = [(0, -0.05, "quoted"), (1, 0.6 if spike else 0.1, "quoted"), (2, 0.2, "quoted"),
                    (5, -1.0 if rng.random() < 0.7 else 1.5, "settled")]
            rows.append(play(s, path))
    out = lrn.walk_forward(rows)
    assert out["test_sessions"] > 30 and out["mean_improvement"] > 0.2 and out["p"] < 0.05
    assert out["chosen"][0][0].startswith("take profit at +50%") or out["chosen"][0][0].startswith("take profit at +25%")
    assert out["best_in_sample"]["mean"] > out["best_in_sample"]["hold_mean"]
    # a fair game (each later mark is the earlier one plus noise): no exit rule beats holding out of sample
    noise = []
    for s in SESSIONS[:80]:
        for _ in range(4):
            first = rng.gauss(0, 0.3)
            noise.append(play(s, [(0, 0.0, "quoted"), (1, first, "quoted"), (3, first + rng.gauss(0, 0.4), "settled")]))
    assert (lrn.walk_forward(noise)["p"] or 1) > 0.01


def test_walk_forward_trains_only_on_plays_resolved_before_each_block() -> None:
    # long-dated plays never resolve before later blocks: nothing can be trained, nothing is tested
    far = [play(s, [(0, 0.0, "quoted"), (1, 0.9, "quoted")], expiration=date(2027, 1, 15)) for s in SESSIONS[:60]]
    out = lrn.walk_forward(far)
    assert out["test_sessions"] == 0 and out["best_in_sample"] is None


# ── challengers ──────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def research(tmp_path: Path) -> Any:
    dbs = StocksDatabases.open(DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env"))
    yield dbs.research
    dbs.close()


def outcomes(conn: sqlite3.Connection, sessions: list[date], base: float, better: float, variant: str = "liquid_only",
             noise: float = 0.05, seed: int = 0) -> None:
    rng = random.Random(seed)
    for s in sessions:
        for rank in (1, 2, 3):
            conn.execute("INSERT INTO stk_play_outcomes VALUES (?, 'edge', ?, 'x', 10, 1, 1, ?)",
                         (day_of(s), rank, base + rng.gauss(0, noise)))
            conn.execute("INSERT INTO stk_play_shadow_outcomes VALUES (?, ?, 'edge', ?, 'x', 10, 1, ?)",
                         (day_of(s), variant, rank, better + rng.gauss(0, noise)))


def test_a_challenger_that_beats_the_live_rules_on_later_sessions_is_promoted(research: sqlite3.Connection) -> None:
    outcomes(research, SESSIONS[:25], base=-0.10, better=0.05)
    tests = lrn.challengers(research, ["edge"])
    winner = next(t for t in tests if t["variant"] == "liquid_only")
    assert winner["sessions"] == 25 and winner["mean_difference"] == pytest.approx(0.15, abs=0.03)
    assert winner["p_holm"] < 0.05 and winner["status"] == "beats the live rules"
    others = [t for t in tests if t["variant"] != "liquid_only"]
    assert all(t["sessions"] == 0 and t["status"].startswith("testing") for t in others)
    before = pl.method(lrn.promoted(research))
    assert lrn.promote(research, tests, NOW) == [{"category": "edge", "variant": "liquid_only"}]
    assert lrn.promoted(research) == {"edge": "liquid_only"}
    assert pl.method(lrn.promoted(research)) != before                    # a promotion is a new rule version
    again = next(t for t in lrn.challengers(research, ["edge"]) if t["variant"] == "liquid_only")
    assert again["status"] == "promoted" and again["p_holm"] is None      # not re-tested against itself
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        research.execute("DELETE FROM stk_play_promotions")


def test_too_few_sessions_or_noise_is_never_promoted_and_failures_stay_visible(research: sqlite3.Connection) -> None:
    outcomes(research, SESSIONS[:12], base=-0.10, better=0.20)          # big but early: 12 of 20 sessions
    early = next(t for t in lrn.challengers(research, ["edge"]) if t["variant"] == "liquid_only")
    assert early["status"] == "testing (12 of 20 sessions)" and lrn.promote(research, [early], NOW) == []
    outcomes(research, SESSIONS[12:45], base=0.0, better=-0.02, noise=0.3, seed=3)
    late = next(t for t in lrn.challengers(research, ["edge"]) if t["variant"] == "liquid_only")
    assert late["sessions"] == 45 and late["status"] == "failed"


def test_holm_corrects_across_every_challenger_tested(research: sqlite3.Connection) -> None:
    rng = random.Random(5)
    for v in lrn.VARIANTS:                                               # pure noise for every challenger
        for s in SESSIONS[:30]:
            research.execute("INSERT OR IGNORE INTO stk_play_shadow_outcomes VALUES (?, ?, 'edge', 1, 'x', 10, 1, ?)",
                             (day_of(s), v.id, rng.gauss(0, 0.2)))
    for s in SESSIONS[:30]:
        research.execute("INSERT INTO stk_play_outcomes VALUES (?, 'edge', 1, 'x', 10, 1, 1, ?)",
                         (day_of(s), rng.gauss(0, 0.2)))
    tests = [t for t in lrn.challengers(research, ["edge"]) if t["p"] is not None]
    assert len(tests) == len(lrn.VARIANTS)
    assert all(t["p_holm"] >= t["p"] for t in tests)
    assert not lrn.promote(research, tests, NOW)


# ── labels ───────────────────────────────────────────────────────────────────────────────────────
def test_categories_are_labelled_proven_failing_or_unproven() -> None:
    rng = random.Random(2)
    rows = []
    for s in SESSIONS[:30]:
        for _ in range(3):
            good = 0.3 + rng.gauss(0, 0.2)
            rows.append(play(s, [(0, -0.05, "quoted"), (4, good, "settled")], "edge",
                             twin=[(4, good - 0.25 + rng.gauss(0, 0.1), "settled")]))
            bad = -0.4 + rng.gauss(0, 0.2)
            rows.append(play(s, [(0, -0.05, "quoted"), (4, bad, "settled")], "lottos",
                             twin=[(4, bad + 0.3 + rng.gauss(0, 0.1), "settled")]))
    for s in SESSIONS[:5]:
        rows.append(play(s, [(0, 0.0, "quoted"), (4, 0.5, "settled")], "leaps"))
    out = lrn.labels(rows, ["edge", "lottos", "leaps"], {"edge": "cheap_iv_calls", "lottos": None, "leaps": None})
    assert out["edge"]["state"] == "proven" and "PAPER as cheap_iv_calls" in out["edge"]["text"]
    assert out["lottos"]["state"] == "failing" and out["lottos"]["text"].startswith("Failing")
    assert out["leaps"]["state"] == "unproven" and "5 of 60 settled plays" in out["leaps"]["text"]


# ── calibration and inputs ───────────────────────────────────────────────────────────────────────
def test_calibration_uses_the_most_specific_group_with_enough_plays(research: sqlite3.Connection) -> None:
    rng = random.Random(4)
    for i in range(80):
        s = SESSIONS[i % 40]
        category, dte = ("edge", 20) if i < 40 else ("lottos", 5)
        exp = s + timedelta(days=dte)
        research.execute(
            "INSERT INTO stk_plays(session, category, rank, symbol, action, structure, expiration, event, event_kind,"
            " event_strike, event_width, entry, max_loss, legs_json, underlying, itm_market, itm_history, itm_kestrel,"
            " itm_kestrel_raw, profit_kestrel, play_json, method, recorded_at) VALUES (?, ?, ?, 'X', 'BUY', 'long_call',"
            " ?, 'itm', 'call', 10, 0, 1, 101, '[]', 10, 0.5, 0.5, 0.5, 0.6, 0.4, ?, 'm', 'x')",
            (day_of(s), category, i, day_of(exp), json.dumps({"odds_version": od.MODEL_VERSION})))
        research.execute("INSERT INTO stk_play_outcomes VALUES (?, ?, ?, 'x', 10, ?, 0, 0)",
                         (day_of(s), category, i, int(rng.random() < (0.9 if category == "edge" else 0.1))))
    groups = pl.calibration(StocksResearch(research))
    edge, level = pl.calibrator(groups, "itm", "edge", 20)
    assert level == "this category at this expiry" and edge.apply(0.6) > 0.7
    lotto, level = pl.calibrator(groups, "itm", "lottos", 5)
    assert level == "this category at this expiry" and lotto.apply(0.6) < 0.4
    other, level = pl.calibrator(groups, "itm", "edge", 200)              # no plays that far out: the category
    assert level == "this category" and other.resolved == 40
    pooled, level = pl.calibrator(groups, "itm", "leaps", 300)
    assert level == "all plays" and pooled.resolved == 80
    assert pl.calibrator(groups, "move", "earnings", 20) == (groups["move"], None)
    summary = pl.calibration_summary(StocksResearch(research))
    assert summary["groups"]["itm|edge|8–30 days"] == {"resolved": 40, "applied": True}


def test_input_information_coefficients_find_what_predicts_returns() -> None:
    rng = random.Random(7)
    rows = []
    for s in SESSIONS[:40]:
        for _ in range(15):
            edge = rng.gauss(0, 1)
            ret = 0.2 * edge + rng.gauss(0, 0.3)
            rows.append(play(s, [(0, 0.0, "quoted"), (5, ret, "quoted"), (9, 0.0, "settled")],
                             features={"itm_kestrel": 0.5 + edge / 10, "itm_market": 0.5, "iv": rng.random()}))
    by = {f["feature"]: f for f in lrn.feature_ics(rows)}
    assert by["edge"]["sessions"] == 40 and by["edge"]["ic"] > 0.3 and by["edge"]["p"] < 0.01
    assert abs(by["iv"]["ic"]) < 0.1
    assert by["spread_pct"]["sessions"] == 0 and by["spread_pct"]["ic"] is None
