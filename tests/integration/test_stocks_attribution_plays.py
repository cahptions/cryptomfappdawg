"""Graded plays split into direction, volatility, time decay and costs from their stored closing marks (desk.stocks
.attribution), aggregated by section, and shown on the play's detail view. Synthetic chains only."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from desk.stocks import attribution as at
from desk.stocks import calendar as cal
from desk.stocks import play_history as ph
from tests.integration.test_stocks_play_history import simulate
from tests.stocks_world import make_world


@pytest.fixture(scope="module")
def graded(tmp_path_factory: pytest.TempPathFactory) -> Any:
    w = make_world(tmp_path_factory.mktemp("attribution"))
    simulate(w)
    try:
        yield w
    finally:
        w.close()


def test_every_graded_play_is_split_and_the_parts_add_up(graded: Any) -> None:
    w = graded
    outcomes = w.research.conn.execute("SELECT COUNT(*) FROM stk_play_outcomes").fetchone()[0]
    assert outcomes > 0
    now = cal.session_close(w.days[-1]) + timedelta(hours=3)
    out = at.run(w.market.conn, w.research.conn, None, now)
    assert out["plays"] + out["skipped"] == outcomes
    assert at.run(w.market.conn, w.research.conn, None, now) == {"paper": 0, "plays": 0, "skipped": 0}   # once
    rows = w.research.conn.execute("SELECT * FROM stk_pl_attribution WHERE total IS NOT NULL").fetchall()
    assert rows
    for r in rows:
        assert sum(r[k] for k in at.PARTS) == pytest.approx(r["total"], abs=0.05)
        assert r["costs"] < 0                                           # entry at the ask and fees always cost
    groups = at.summary(w.research.conn, {"lottos": "Lottos"})
    lotto = next(g for g in groups if g["group"] == "lottos")
    assert lotto["title"] == "Lottos" and lotto["trades"] >= 1 and lotto["words"]
    main = next(r for r in rows if "@" not in r["grp"])                 # a section's own pick (not an expiry cohort)
    s, c, rank = main["ref"].split(":")
    view = ph.play_view(w.research.conn, date.fromordinal(int(s)), c, int(rank), now)
    assert view["attribution"]["total"] == pytest.approx(main["total"]) and view["attribution"]["words"]


def test_the_learning_report_says_where_the_money_came_from_and_what_it_is_trying(graded: Any) -> None:
    from desk.stocks import learning_report as lr

    w = graded
    at.run(w.market.conn, w.research.conn, None, cal.session_close(w.days[-1]) + timedelta(hours=3))
    view = {"learned_trials": [
        {"id": "learned_abcd1234", "words": "Long calls expiring about a month (22–45 days)", "status": "live",
         "evidence": {"discovery": {"mean": 0.12, "days": 30}},
         "forward": {"plays": {"days": 3, "trades": 9}, "paper": {"days": 2, "trades": 4}},
         "forward_words": "Plays: +5% average over 3 graded days (9 trades); judged from 20 days."},
        {"id": "learned_ffff0000", "words": "Long puts", "status": "retired",
         "retired": {"reason": "Retired: its finding is retired."}, "forward_words": "No forward record yet."}],
        "trials_waiting": 2,
        "graduation": {"graduated": False, "why": "Not graduated: this version must first beat its random twin."}}
    report = lr.compose(lr.Sources(w.research.conn, w.market.conn, None, {"learner_account": view}),
                        cal.session_close(w.days[-1]) + timedelta(hours=4))
    sections = {s["id"]: s for s in report["sections"]}
    assert [s["title"] for s in report["sections"]][1:3] == ["What it's trying next",
                                                             "What it learned from its own trades"]
    trying = [i["headline"] for i in sections["trying"]["items"]]
    assert "Trying: Long calls expiring about a month (22–45 days)." in trying
    assert "Stopped trying: Long puts." in trying and "2 more confirmed findings wait for a trial slot." in trying
    assert "The Learner's own picks are a preview on the Learner page (not graduated)." in trying
    live = next(i for i in sections["trying"]["items"] if i["headline"].startswith("Trying:"))
    assert "So far — Plays: +5% average" in live["detail"] and live["strength"] == "early"
    own = sections["own_trades"]["items"]
    heads = [i["headline"] for i in own]
    assert any(h.startswith("Where the money came from — ") and h.endswith(" per contract.") for h in heads), heads
    assert not any(" band:" in h for h in heads)                       # sections, not their expiry cohorts
    for i in own:
        assert "unexplained" in i["detail"] or i["key"].startswith("own:lesson")
