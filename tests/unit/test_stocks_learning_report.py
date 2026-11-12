"""The Learning report: every finding with its evidence and an honest strength, replays kept apart, nothing from
estimated marks, "waiting for" text on empty databases, read-only, and one weekly snapshot."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from desk.core.paths import DeskPaths
from desk.stocks import calendar as cal
from desk.stocks import learning
from desk.stocks import learning_report as lr
from desk.stocks import odds as od
from desk.stocks.paper.book import PaperBook
from desk.stocks.repo import StocksDatabases
from tests.conftest import REPO

NOW = datetime(2026, 11, 12, 21, 0, tzinfo=UTC)          # a Thursday evening in New York
SESSIONS = cal.trading_days(date(2026, 8, 3), date(2026, 11, 11))


@pytest.fixture
def desk(tmp_path: Path) -> Any:
    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env")
    dbs = StocksDatabases.open(paths)
    yield paths, dbs
    dbs.close()


def play(conn: sqlite3.Connection, session: date, rank: int, *, shown: float, hit: bool | None, raw: float | None = None,
         net: float = 0.0, category: str = "trend_calls", symbol: str = "AAA", kind: str = "call",
         market: float | None = None, event: str = "itm", dte: int = 20) -> None:
    raw = shown if raw is None else raw
    expiration = session + timedelta(days=dte)
    conn.execute("INSERT INTO stk_plays(session, category, rank, symbol, action, structure, expiration, event, event_kind, "
                 "event_strike, event_width, entry, max_loss, legs_json, underlying, itm_market, itm_history, itm_kestrel, "
                 "itm_kestrel_raw, profit_kestrel, play_json, method, recorded_at) "
                 "VALUES (?, ?, ?, ?, 'BUY', 'long_call', ?, ?, ?, 100, 0, 1.5, 150, '[]', 100, ?, NULL, ?, ?, NULL, "
                 "?, 'm', ?)",
                 (session.toordinal(), category, rank, symbol, expiration.toordinal(), event, kind, market, shown, raw,
                  json.dumps({"odds_version": od.MODEL_VERSION}), session.isoformat()))
    if hit is not None:
        conn.execute("INSERT INTO stk_play_outcomes VALUES (?, ?, ?, ?, 100, ?, 0, ?)",
                     (session.toordinal(), category, rank, expiration.isoformat() + "T21:00:00+00:00", int(hit), net))


def items(report: dict[str, Any], section: str) -> list[dict[str, Any]]:
    return next(s["items"] for s in report["sections"] if s["id"] == section)


def find(report: dict[str, Any], section: str, text: str) -> dict[str, Any]:
    return next(i for i in items(report, section) if text in i["headline"])


def test_strength_needs_the_loops_threshold_and_separate_days() -> None:
    assert lr.strength(30, 2, need=30) == "early"           # 30 plays from 2 sessions: early
    assert lr.strength(29, 20, need=30) == "early"
    assert lr.strength(30, 10, need=30) == "suggestive"
    assert lr.strength(3, 1, need=30, tested=True) == "tested"


def test_overconfidence_is_detected_with_its_evidence_and_calibration_now_shown(desk: Any) -> None:
    paths, dbs = desk
    for i, session in enumerate(SESSIONS[:20]):
        for rank in range(3):                                   # said ~65%, only 30% finished in the money
            play(dbs.research, session, rank, shown=0.65, hit=(i * 3 + rank) % 10 < 3, market=0.55)
    report = lr.build(paths, NOW)
    over = find(report, "wrong", "Over-confident on finishing in the money")
    assert "it showed 65% on average and 30% finished in the money" in over["headline"]
    assert over["evidence"] == {"items": 60, "days": 20, "unit": "plays", "day_unit": "sessions"}
    assert over["strength"] == "suggestive" and over["link"] == "#plays/performance"
    cell = find(report, "learned", "When KESTREL said 60–70% for Trend calls")
    assert "30% finished in the money (60 plays over 20 sessions)" in cell["headline"]
    shows = float(cell["headline"].split("about ")[-1].rstrip("%."))
    assert 30 < shows < 65                                    # pulled toward what happened, shrunk by the prior
    assert find(report, "achievements", "now calibrated")["evidence"]["items"] == 60
    market = find(report, "behaviour", "Against the market's own odds")
    assert "10 points above the market's (more bullish" in market["headline"]
    assert find(report, "wrong", "The market's own odds have been more accurate")


def test_thirty_plays_from_two_sessions_are_early(desk: Any) -> None:
    paths, dbs = desk
    for session in SESSIONS[:2]:
        for rank in range(15):
            play(dbs.research, session, rank, shown=0.7, hit=rank < 3)
    report = lr.build(paths, NOW)
    over = find(report, "wrong", "Over-confident on finishing in the money")
    assert over["evidence"]["items"] == 30 and over["evidence"]["days"] == 2 and over["strength"] == "early"
    assert all(i["strength"] == "early" for s in report["sections"] for i in s["items"])


def test_nothing_is_produced_from_estimated_marks(desk: Any) -> None:
    paths, dbs = desk
    for rank, session in enumerate(SESSIONS[-10:]):
        play(dbs.research, session, rank, shown=0.9, hit=None)   # not decided: no outcome, no grade
        for day in (session, SESSIONS[-1]):
            dbs.research.execute("INSERT OR IGNORE INTO stk_play_marks VALUES (?, 'trend_calls', ?, 0, ?, 'estimated', "
                                 "9.0, 9.0, 800, 5.0, 100, NULL, ?)",
                                 (session.toordinal(), rank, day.toordinal(), session.isoformat()))
    report = lr.build(paths, NOW)
    texts = " ".join(i["headline"] for s in report["sections"] for i in s["items"])
    assert "Biggest winner" not in texts and "Over-confident" not in texts
    assert report["headline"][0].startswith("KESTREL is still collecting: no play has been graded")
    assert "plays.graded" not in report["metrics"] or report["metrics"]["plays.graded"]["value"] == 0
    weak = find(report, "wrong", "estimated or modelled")
    assert weak["headline"].startswith("100% of the last 90 days' play marks")


def test_replays_stay_in_their_own_section(desk: Any) -> None:
    paths, _ = desk
    (paths.home / "stocks-option-research.json").write_text(json.dumps({"strategies": {"vol_gap_straddles": {
        "title": "Vol-gap straddles", "trades": 48, "return": 0.4,
        "versus_baseline": {"days": 40, "mean_gap": 0.05, "p_not_better": 0.01}}}}))
    (paths.home / "stocks-lotto-history.json").write_text(json.dumps({"sessions": 120, "variants": {"kestrel_run": {
        "picks": 300, "best": {"symbol": "NVDA", "multiple": 14.0}}},
        "kestrel_vs_random": {"run": {"days": 90, "mean_gap": 0.2, "p_not_better": 0.3}}}))
    report = lr.build(paths, NOW)
    replay = items(report, "replay")
    research = next(i for i in replay if "Vol-gap straddles" in i["headline"])
    assert research["headline"].startswith("Preregistered replay") and research["strength"] == "tested"
    lotto = next(i for i in replay if "Lotto replay" in i["headline"])
    assert "NVDA paid 14.0×" in lotto["headline"] and lotto["strength"] == "suggestive"
    forward = [i for s in report["sections"] if s["id"] != "replay" for i in s["items"]]
    assert not any("replay" in i["headline"].lower() or "NVDA" in i["headline"] for i in forward)
    assert not any("replay" in sentence.lower() for sentence in report["headline"])
    markdown = lr.render_markdown(report)
    assert markdown.index("## Replays on past data") > markdown.index("## Coming up")


def test_empty_databases_say_what_they_are_waiting_for(tmp_path: Path) -> None:
    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env")
    for report in (lr.build(paths, NOW), None):                # no databases at all, then freshly created ones
        if report is None:
            StocksDatabases.open(paths).close()
            learning.open_book(paths).close()
            report = lr.build(paths, NOW)
        waiting = " ".join(i["headline"] + " " + i["detail"] for i in items(report, "coming"))
        for loop in ("PAPER strategies: waiting", "Evidence: waiting", "Learning workspace: waiting"):
            assert loop in waiting
        assert "Plays odds: waiting" in waiting or report["metrics"] == {}
        assert report["headline"] == ["KESTREL is still collecting: no play has been graded and no Learning "
                                      "position has finished yet."]
        assert items(report, "changed")[0]["headline"].startswith("No earlier weekly snapshot")
        assert "# KESTREL Learning report" in lr.render_markdown(report)
        assert items(report, "wrong") == []                     # nothing unreadable, nothing invented


def test_plays_learning_verdicts_exits_and_promotions_are_tested_only_where_its_tests_say_so(desk: Any) -> None:
    paths, dbs = desk
    for session in SESSIONS[:25]:
        play(dbs.research, session, 1, shown=0.5, hit=True, net=0.3, category="bear_puts", kind="put")
    report_json = {"session": "2026-11-11", "labels": {
        "bear_puts": {"state": "proven", "settled": 80, "sessions": 25, "mean": 0.12, "vs_twins": 0.08, "text": "Proven"},
        "lottos": {"state": "failing", "settled": 70, "sessions": 22, "mean": -0.4, "vs_twins": -0.2, "text": "Failing"},
        "trend_calls": {"state": "unproven", "settled": 12, "sessions": 6, "mean": -0.1, "vs_twins": -0.06}},
        "exits": {"bear_puts": {"suggested": True, "test_sessions": 22, "mean_improvement": 0.07, "p_holm": 0.01,
                                "dte_bucket": "8–30 days", "best_in_sample": {"rule": "take profit at +50%",
                                                                              "mean": 0.2, "hold_mean": 0.1}}},
        "features": [{"feature": "edge", "label": "KESTREL minus market chance", "sessions": 30, "ic": 0.08, "p": 0.01}],
        "challengers": [{"variant": "tight_spread", "title": "Spreads under 6%", "category": "bear_puts",
                         "sessions": 21, "status": "promoted", "mean_difference": 0.05},
                        {"variant": "liquid_only", "title": "Liquid contracts only", "category": "lottos",
                         "sessions": 12, "status": "testing (12 of 20 sessions)", "mean_difference": 0.01}]}
    dbs.research.execute("INSERT INTO stk_play_learning(created_at, session, method, report_json) VALUES (?, ?, 'm', ?)",
                         (NOW.isoformat(), SESSIONS[-1].toordinal(), json.dumps(report_json)))
    report = lr.build(paths, NOW)
    assert find(report, "achievements", "Bearish puts is proven")["strength"] == "tested"
    assert find(report, "wrong", "Lottos is failing")["strength"] == "tested"
    trails = find(report, "wrong", "Trend calls trails its random twins")
    assert trails["strength"] == "early" and "not significant yet" in trails["headline"]
    exit_rule = find(report, "learned", "Exit rule for Bearish puts: take profit at +50%")
    assert exit_rule["strength"] == "tested" and exit_rule["evidence"]["days"] == 22
    assert find(report, "learned", "KESTREL minus market chance predicts")["strength"] == "suggestive"
    assert find(report, "achievements", "Promotion: \"Spreads under 6%\"")["strength"] == "tested"
    assert find(report, "coming", "Challenger \"Liquid contracts only\" for Lottos: 12 of 20 sessions")


def test_paper_grades_and_learning_holding_times(desk: Any) -> None:
    paths, _ = desk
    paper = {"accounts": [
        {"id": "momentum", "title": "Momentum", "baseline": False, "sessions": 90, "trades": 40, "return": 0.08,
         "spy_return": 0.03, "grade": {"grade": "working", "text": "Beat its baseline"},
         "best": {"symbol": "NVDA", "pnl": 900.0}, "invested": 0.9},
        {"id": "value", "title": "Value", "baseline": False, "sessions": 90, "trades": 30, "return": -0.05,
         "spy_return": 0.03, "grade": {"grade": "backwards", "text": "Behind its baseline by more than chance"}},
        {"id": "news", "title": "News", "baseline": False, "sessions": 40, "trades": 5, "return": 0.01,
         "spy_return": 0.02, "grade": {"grade": "early", "text": "Too early"}, "style": "daily"},
        {"id": "random_10", "title": "Random", "baseline": True, "sessions": 90, "grade": {"grade": "info"}}]}
    (paths.home / "stocks-paper.json").write_text(json.dumps(paper))
    conn = learning.open_book(paths)
    try:
        at = datetime(2026, 10, 1, 15, tzinfo=UTC)
        learning.initialize(conn, at)
        recipe = next(r["id"] for r in conn.execute("SELECT id, config_json FROM stk_learning_experiments") if json.loads(
            r["config_json"]).get("bucket") == "1m" and json.loads(r["config_json"])["structure"] == "long_call")
        book = PaperBook(conn)
        for i in range(12):                                      # 12 positions on 12 separate days
            day = at + timedelta(days=i)
            did = book.decide(day, day.date(), learning.RESEARCH, "spec", "SPY", "ENTER", ["Synthetic"], {})
            pid = book.open_position(account=learning.RESEARCH, spec_sha256="spec", asset="option", symbol="SPY",
                                     structure="long_call", legs=[], decision_id=did, at=day, day=day.date(), cost=100.0,
                                     fees=0.0, max_loss=100.0, stop_price=None, expires=date(2026, 12, 18),
                                     plan={"experiment_id": recipe})
            for hold, ret in ((1, -0.05), (7, 0.12), (14, 0.02)):
                conn.execute("INSERT INTO stk_learning_checkpoints VALUES (?, ?, ?, ?, ?, 100)",
                             (pid, hold, (day + timedelta(days=hold)).isoformat(), 100 * (1 + ret), ret))
    finally:
        conn.close()
    report = lr.build(paths, NOW)
    assert find(report, "achievements", "PAPER: Momentum is working")["strength"] == "tested"
    assert find(report, "wrong", "PAPER: Value is doing the opposite")["evidence"] == {
        "items": 30, "days": 90, "unit": "closed trades", "day_unit": "sessions"}
    assert find(report, "coming", "First PAPER grade for News: 40 of 63 sessions")
    best = find(report, "learned", "Best holding time so far for 1-month calls: 7 days (+12.0% on average")
    assert best["evidence"]["items"] == 12 and best["evidence"]["days"] == 12 and best["strength"] == "suggestive"


def test_build_is_read_only(desk: Any) -> None:
    paths, dbs = desk
    for i, session in enumerate(SESSIONS[:12]):
        play(dbs.research, session, 1, shown=0.6, hit=i % 2 == 0)
    before = list(dbs.research.iterdump())
    lr.build(paths, NOW)
    assert list(dbs.research.iterdump()) == before
    assert not dbs.research.execute("SELECT 1 FROM stk_learning_reports").fetchone()     # never on a GET


def test_one_weekly_snapshot_after_fridays_close_feeds_changed_since_last_week(desk: Any) -> None:
    paths, dbs = desk
    thursday, friday_noon = NOW, datetime(2026, 11, 13, 17, tzinfo=UTC)
    friday_evening, saturday = datetime(2026, 11, 13, 22, tzinfo=UTC), datetime(2026, 11, 14, 15, tzinfo=UTC)
    assert not lr.snapshot_due(thursday) and not lr.snapshot_due(friday_noon)
    assert lr.snapshot_due(friday_evening) and lr.snapshot_due(saturday)
    assert not lr.weekly(paths, dbs.research, thursday)
    assert lr.weekly(paths, dbs.research, friday_evening)
    assert not lr.weekly(paths, dbs.research, saturday)                     # at most once a week
    assert dbs.research.execute("SELECT week FROM stk_learning_reports").fetchall()[0][0] == "2026-W46"
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        dbs.research.execute("DELETE FROM stk_learning_reports")
    # a week later, with plays graded since: the change is reported against that snapshot
    for i, session in enumerate(SESSIONS[:12]):
        play(dbs.research, session, 1, shown=0.6, hit=i % 2 == 0)
    later = lr.build(paths, friday_evening + timedelta(days=7))
    assert str(later["previous"]).startswith("2026-11-13T22:00")
    changed = " ".join(i["headline"] for i in items(later, "changed"))
    assert "Plays graded at expiry: 0 → 12." in changed
    assert later["snapshots"][0]["week"] == "2026-W46"
    # the same week's snapshot is never "last week"
    assert lr.build(paths, saturday)["previous"] is None


def test_a_tiny_brier_gap_with_the_market_is_called_a_tie_not_a_win(desk: Any) -> None:
    paths, dbs = desk
    for i, session in enumerate(SESSIONS[:12]):
        for rank in range(3):                                   # 60% against the market's 61%: noise, not skill
            play(dbs.research, session, rank, shown=0.60, hit=(i * 3 + rank) % 10 < 6, market=0.61)
    report = lr.build(paths, NOW)
    assert find(report, "behaviour", "about as accurate as the market's own")
    assert not [i for i in items(report, "achievements") if "beat the market" in i["headline"]]
    assert not [i for i in items(report, "wrong") if "market's own odds have been more accurate" in i["headline"]]


def test_learning_promotions_are_described_by_their_stage(desk: Any) -> None:
    """Only a confirmed recipe is called a finding; a provisional one is 'confirming', a failed confirmation stays in
    "What it's getting wrong", and a promotion from before the 2026-11-13 rules is labelled provisional."""
    from desk.stocks import learning_promotion as promo

    paths, _ = desk
    conn = learning.open_book(paths)
    try:
        at = datetime(2026, 10, 1, 15, tzinfo=UTC)
        learning.initialize(conn, at)
        ids = [r[0] for r in conn.execute("SELECT id FROM stk_learning_experiments ORDER BY id")]
        for ident, stage, detail in (
                (ids[0], "confirmed", {"window": ["2026-10-01", "2026-10-28"], "days": 20, "mean": 0.03, "p": 0.01,
                                       "promotion_id": 1, "alpha": 0.05, "fresh_after": "2026-10-28"}),
                (ids[1], "provisional", {"promotion_day": "2026-11-02", "days": 22, "first_day": "2026-09-01",
                                         "mean": 0.02, "p": 0.001, "holm_threshold": 0.0033, "recipes_compared": 15,
                                         "alpha": 0.05, "confirmation": {"entry_days": 20, "after": "2026-11-02"}}),
                (ids[2], "failed", {"window": ["2026-09-01", "2026-09-29"], "days": 20, "mean": -0.01, "p": 0.7,
                                    "promotion_id": 2, "alpha": 0.05, "fresh_after": "2026-09-29"})):
            promo._record(conn, ident, at, stage, detail)
        promo.relabel_legacy(conn, {"champion": "long_put", "test_blocks": 25}, "2026-09-15", at)
        learning.set_state(conn, "model", {"version": 3, "rules": promo.RULES, "champion": "long_call"})
    finally:
        conn.close()
    report = lr.build(paths, NOW)
    confirmed = find(report, "achievements", "Learning confirmed a finding")
    assert confirmed["strength"] == "tested" and "untouched entry days" in confirmed["detail"]
    provisional = find(report, "coming", "Confirming ")
    assert provisional["strength"] == "suggestive" and "Not a finding until confirmed" in provisional["detail"]
    assert "0 of 20 untouched" in provisional["headline"]
    assert find(report, "wrong", "failed its confirmation")["strength"] == "tested"
    legacy = find(report, "learned", "An earlier Learning promotion of long puts")
    assert "provisional (before 2026-11-13 rules)" in legacy["headline"] and legacy["strength"] == "suggestive"
    assert not any("Learning promoted" in i["headline"] for s in report["sections"] for i in s["items"])
