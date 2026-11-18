"""What the Learner tries next (desk.stocks.learner.trials): a confirmed "do" finding becomes a frozen, fingerprinted
trial (a Plays section and a PAPER account), capped, counted as a trial and retired when its finding retires or its
forward record fails; a confirmed "don't" puts a warning on the plays it describes. Synthetic chains only."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import numpy as np
import pytest

from desk.core.hashing import canonical_json
from desk.stocks import calendar as cal
from desk.stocks import plays as pl
from desk.stocks.contracts import RatePoint
from desk.stocks.learner import actions as act
from desk.stocks.learner import experience as ex
from desk.stocks.learner import findings as fd
from desk.stocks.learner import overfit as of
from desk.stocks.learner import policy
from desk.stocks.learner import trials as tr
from desk.stocks.paper.book import PaperBook
from tests.stocks_world import add_chains, make_world

SYMBOLS = ["SPY", "CHEAP", "SQZ", "S10", "S11", "S12"]
NOW = datetime(2026, 10, 22, 2, tzinfo=UTC)
CALL_MONTH = {"structure": act.STRUCTURE_CODE["long_call"], "conditions": [{"f": "bucket", "in": [2]}]}
STRADDLES = {"structure": act.STRUCTURE_CODE["long_straddle"], "conditions": []}


def closing(d: date) -> datetime:
    return cal.session_close(d) + timedelta(minutes=30)


def add_finding(conn: Any, rule: dict[str, Any], kind: str = "do", status: str = "confirmed",
                at: str = "2026-10-01T00:00:00+00:00", mean: float = 0.12) -> str:
    fid = fd.rule_id(rule)
    ev = {"mean": mean if kind == "do" else -abs(mean), "low": 0.03, "high": 0.2, "excess": -0.1, "excess_low": -0.2,
          "excess_high": -0.02, "rows": 400, "days": 30, "first": "2026-06-01", "last": "2026-08-01",
          "deflated": {"dsr": 0.97, "trials": 240, "passes": True}}
    conn.execute("INSERT OR IGNORE INTO stk_learn_findings(id, kind, rule_json, words, rules_version, model_id, "
                 "evidence_json, first_session, last_session, found_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (fid, kind, canonical_json(rule), fd.sentence(kind, rule, ev), fd.version_hash(), "model-a",
                  json.dumps(ev), 1, 2, at))
    conn.execute("INSERT OR IGNORE INTO stk_learn_finding_status VALUES (?,?,?,?,?,?,?)",
                 (fid, at, "candidate", "model-a", 1, 2, json.dumps(ev)))
    if status != "candidate":
        conn.execute("INSERT OR IGNORE INTO stk_learn_finding_status VALUES (?,?,?,?,?,?,?)",
                     (fid, at.replace("T00", "T01"), "confirmed", "model-a", 3, 4, json.dumps({**ev, "days": 12})))
    if status == "retired":
        conn.execute("INSERT OR IGNORE INTO stk_learn_finding_status VALUES (?,?,?,?,?,?,?)",
                     (fid, at.replace("T00", "T02"), "retired", "model-a", 5, 6, json.dumps(ev)))
    return fid


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> Any:
    w = make_world(tmp_path_factory.mktemp("trials"), n=14)
    add_chains(w, SYMBOLS, w.days[-6:], max_days=120)
    for d in w.days[-6:-1]:
        pl.build(w.market, w.research, closing(d))
        ex.record_forward(w.market, w.research, d, closing(d))
    try:
        yield w
    finally:
        w.close()


def test_a_trial_is_a_deterministic_fingerprinted_function_of_its_finding() -> None:
    f = {"id": fd.rule_id(CALL_MONTH), "rule": CALL_MONTH, "rules_version": "v"}
    a, b = tr.derive(f), tr.derive(json.loads(json.dumps(f)))
    assert a == b and a["id"] == "learned_" + f["id"][:8] and len(a["fingerprint"]) == 16
    assert a["words"] == fd.describe(CALL_MONTH) and a["rule"] == CALL_MONTH
    other = tr.derive({**f, "rule": {**CALL_MONTH, "conditions": [{"f": "bucket", "in": [3]}]}})
    assert other["fingerprint"] != a["fingerprint"]
    st = tr.strategy(a)
    assert st.style == "learner" and st.learned and st.p["fingerprint"] == a["fingerprint"]
    assert st.p["compare"] == policy.RANDOM and st.p["exit_rule"] == "hold"


def test_confirmed_dos_become_trials_capped_and_counted(tmp_path: Any) -> None:
    w = make_world(tmp_path, n=14)
    try:
        conn = w.research.conn
        before = of.counter(conn, "finding")[0]
        made = []
        for i in range(tr.MAX_LIVE + 2):
            made.append(add_finding(conn, {"structure": act.STRUCTURE_CODE["long_call"],
                                           "conditions": [{"f": "iv_rank", "gt": None, "le": float(10 + 5 * i)}]},
                                    at=f"2026-10-0{i + 1}T00:00:00+00:00"))
        add_finding(conn, STRADDLES, kind="dont")                      # a don't never becomes a trial
        add_finding(conn, {**CALL_MONTH, "conditions": [{"f": "bucket", "in": [5]}]}, status="candidate")
        out = tr.sync(conn, w.dbs.ledger, NOW, w.market)
        assert len(out["created"]) == tr.MAX_LIVE and out["waiting"] == 2
        assert {t["finding"] for t in tr.live(conn)} == set(made[:tr.MAX_LIVE])      # oldest confirmation first
        assert of.counter(conn, "finding")[0] == before + tr.MAX_LIVE                # counted as trials
        assert tr.sync(conn, w.dbs.ledger, NOW, w.market)["created"] == []            # idempotent
        # A retired finding retires its trial, and the oldest waiting one takes the slot.
        conn.execute("INSERT INTO stk_learn_finding_status VALUES (?,?,?,?,?,?,?)",
                     (made[0], "2026-10-20T00:00:00+00:00", "retired", "model-b", 9, 10, "{}"))
        out = tr.sync(conn, w.dbs.ledger, NOW + timedelta(days=1), w.market)
        assert out["retired"] == ["learned_" + made[0][:8]] and out["created"] == ["learned_" + made[5][:8]]
        gone = next(s for s in tr.specs(conn) if s["finding"] == made[0])
        assert gone["status"] == "retired" and "finding is retired" in gone["retired"]["reason"]
        assert len(tr.live(conn)) == tr.MAX_LIVE
        _, retired = tr.strategies(conn)
        assert retired == {gone["id"]}
        # A failing forward record retires a trial: 20 graded sessions averaging below zero.
        victim = tr.live(conn)[0]["id"]
        for k in range(tr.FAIL_DAYS):
            conn.execute("INSERT INTO stk_play_outcomes VALUES (?,?,?,?,?,?,?,?)",
                         (700_000 + k, victim, 1, "2026-10-20T00:00:00+00:00", 100.0, 0, 0.0, -0.4))
        rec = tr.forward(conn, w.dbs.ledger, victim)
        assert rec["plays"]["days"] == tr.FAIL_DAYS and rec["plays"]["fails"]
        out = tr.sync(conn, w.dbs.ledger, NOW + timedelta(days=2), w.market)
        assert victim in out["retired"] and out["created"] == ["learned_" + made[6][:8]]
        why = next(s for s in tr.specs(conn) if s["id"] == victim)["retired"]["reason"]
        assert "Plays section averaged at or below zero" in why
    finally:
        w.close()


def test_the_plays_trial_section_holds_only_matching_trades_and_is_recorded(world: Any) -> None:
    conn = world.research.conn
    add_finding(conn, CALL_MONTH)
    tr.sync(conn, world.dbs.ledger, NOW, world.market)
    spec = tr.live(conn)[0]
    session = world.days[-1]
    board = pl.build(world.market, world.research, closing(session), ledger=PaperBook(world.dbs.ledger))
    cat = next(c for c in board["categories"] if c["id"] == spec["id"])
    assert cat["title"] == f"Learned: {spec['words']}" and cat["trial"]["label"].startswith("Trial:")
    assert cat["trial"]["fingerprint"] == spec["fingerprint"]
    assert cat["trial"]["evidence"]["discovery"]["mean"] == pytest.approx(0.12)
    assert cat["plays"], "the synthetic chains have month calls"
    assert len({p["symbol"] for p in cat["plays"]}) == len(cat["plays"])            # one a stock
    for p in cat["plays"]:
        assert p["structure"] == "long_call" and 22 <= p["dte"] <= 45
        assert p["exit_plan"]["take_profit"] is None and "held to expiry" in p["exit_plan"]["why"]
    stored = conn.execute("SELECT COUNT(*) FROM stk_plays WHERE session=? AND category=?",
                          (session.toordinal(), spec["id"])).fetchone()[0]
    assert stored == len(cat["plays"])
    again = pl.build(world.market, world.research, closing(session) + timedelta(minutes=5), previous=None)
    assert [p["legs"] for p in next(c for c in again["categories"] if c["id"] == spec["id"])["plays"]] == \
        [p["legs"] for p in cat["plays"]]                                           # a seeded, reproducible sample


def test_a_dont_finding_warns_on_every_matching_play(world: Any) -> None:
    conn = world.research.conn
    add_finding(conn, STRADDLES, kind="dont")
    board = pl.build(world.market, world.research, closing(world.days[-1]) + timedelta(minutes=9))
    plays = [p for c in board["categories"] for p in c["plays"]]
    assert any(p["structure"] == "long_straddle" for p in plays)
    for p in plays:
        if p["structure"] == "long_straddle":
            assert p["dont_warning"]["finding"] == fd.rule_id(STRADDLES)
            assert "did worse than a random candidate" in p["dont_warning"]["text"]
        else:
            assert "dont_warning" not in p


def test_the_trial_account_trades_only_matching_candidates_held_to_expiry(world: Any) -> None:
    conn = world.research.conn
    session = world.days[-2]
    spec = tr.live(conn)[0]
    out = tr.decide(conn, world.dbs.ledger, world.market, session, closing(session))
    assert out[spec["id"]]["status"] == "traded", out
    assert tr.decide(conn, world.dbs.ledger, world.market, session, closing(session)) == {"decided": False}
    rows = world.dbs.ledger.execute("SELECT * FROM stk_learner_decisions WHERE account=?", (spec["id"],)).fetchall()
    assert rows
    for r in rows:
        assert r["exit_rule"] == "hold" and r["mode"] == "random" and r["model_id"] is None
        a = act.parse_key(r["action_key"])
        assert a.structure == "long_call" and a.bucket == "1m"
        assert r["risk_usd"] <= policy.TRADE_RISK * r["equity_usd"] + 0.01
    orders = [o for o in PaperBook(world.dbs.ledger).pending(spec["id"])]
    assert orders and all(o.order["plan"]["exit_rule"] == "hold" for o in orders)
    late = tr.decide(conn, world.dbs.ledger, world.market, world.days[-3],
                     datetime.combine(world.days[-1], time(15), UTC))
    assert late[spec["id"]]["status"] == "waiting" and "too late" in late[spec["id"]]["why"]


def test_trial_features_read_nothing_after_the_decision(world: Any) -> None:
    session = world.days[-2]
    tables = [tb for u in pl.load(world.market, world.research, session)
              if (tb := pl.table(u, pl.Context(session, NOW, 0.04, None, 0.0, None, {}, []))) is not None]
    first = tr.Featurer(world.market, world.research, tables, session, NOW, 0.04)
    before = {tb.u.symbol: first.stock(tb) for tb in tables}
    later = [d for d in world.days if d >= session]
    for d in later:                               # wild values dated on and after the decision session
        for sym in SYMBOLS:
            world.research.conn.execute("INSERT OR REPLACE INTO stk_score_history(asof, symbol, scores_json) "
                                        "VALUES (?,?,?)", (d.toordinal(), sym, json.dumps({"c": 99.0, "momentum": 99.0})))
    world.market.save_rates([RatePoint(series, d, 99.0) for d in later for series in
                             ("VIXCLS", "BAMLH0A0HYM2", "UST10Y", "UST3M")])
    second = tr.Featurer(world.market, world.research, tables, session, NOW, 0.04)
    assert {tb.u.symbol: second.stock(tb) for tb in tables} == before
    assert second.market_x == first.market_x


def test_the_prefilter_never_drops_a_match() -> None:
    rule = {"structure": 0, "conditions": [{"f": "iv_rank", "gt": 10.0, "le": 30.0}, {"f": "dte", "gt": None,
                                                                                      "le": 40.0}]}
    assert tr._prefilter(rule, {"iv_rank": 20.0})
    assert not tr._prefilter(rule, {"iv_rank": 31.0}) and not tr._prefilter(rule, {"iv_rank": None})
    assert tr._prefilter(rule, {})                                  # unknown before pricing: kept for the full check
    X = np.full((1, len(fd.md.FEATURES)), np.nan, dtype=np.float32)
    assert not fd.matches(rule, X)[0]


def test_the_learner_picks_are_a_plays_section_only_once_it_has_graduated(world: Any) -> None:
    from desk.stocks.learner import graduation as gr

    conn, session = world.research.conn, world.days[-2]
    book = PaperBook(world.dbs.ledger)
    options = [o for o in policy.session_candidates(conn, session) if o.row["action"] == "BUY"]
    pricer = policy.Pricer(world.market, session)
    chosen = []
    for o in options:
        if o.symbol in {c.option.symbol for c in chosen}:
            continue
        o.legs = pricer(o)
        if o.legs is None:
            continue
        o.scores = {"expected": 0.2, "lcb": 0.05}
        chosen.append(policy.Choice(o, "exploit", 1, o.risk_unit, "Exploit: synthetic test pick", []))
        if len(chosen) == 3:
            break
    with book.atomic():
        for c in chosen:
            policy._place(book, policy.LEARNER, session, closing(session), c, None, policy.CAPITAL, "uptrend")
    board = pl.build(world.market, world.research, closing(world.days[-1]) + timedelta(minutes=13), ledger=book)
    assert gr.SECTION not in [c["id"] for c in board["categories"]]                    # a preview only, until then
    assert len(gr.picks(world.dbs.ledger, world.days[-1])["items"]) == len(chosen)
    code, pv = gr.current()
    conn.execute("INSERT INTO stk_learn_promotions(model_code, policy_version, stage, at, detail_json) VALUES "
                 "(?,?,?,?,?)", (code, pv, "confirmed", NOW.isoformat(), "{}"))
    gr.step(conn, book, {}, NOW)
    board = pl.build(world.market, world.research, closing(world.days[-1]) + timedelta(minutes=14), ledger=book)
    cat = next(c for c in board["categories"] if c["id"] == gr.SECTION)
    assert cat["title"] == "Learner picks" and cat["learner"]["graduated"] and "Graduated" in cat["blurb"]
    shown = [p["symbol"] for p in cat["plays"]]       # a pick whose contract is not on today's chain is left out
    assert shown and shown == [c.option.symbol for c in chosen if c.option.symbol in shown]
    by = {c.option.symbol: c for c in chosen}
    for p in cat["plays"]:
        c = by[p["symbol"]]
        assert sorted(leg["occ"] for leg in p["legs"]) == sorted(occ for occ, _ in c.option.row["legs"])
        assert p["learner_pick"]["reason"] == "Exploit: synthetic test pick"
