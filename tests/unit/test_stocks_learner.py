"""The Learner's experience log (desk.stocks.learner; docs/STOCKS_LEARNER.md): the action space, the candidates
recorded at a closing build, point-in-time features, labels from stored closing quotes only, the replay examples and
the exploration slice. Network-free, on the synthetic world."""

import json
import sqlite3
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from desk.stocks import calendar as cal
from desk.stocks import plays as pl
from desk.stocks.contracts import Bar, RatePoint, ShortInterest
from desk.stocks.learner import actions as act
from desk.stocks.learner import experience as ex
from desk.stocks.learner import features as ft
from desk.stocks.learner import labels as lb
from desk.stocks.learner import replay as rp
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


def test_the_action_space_is_dials_with_a_version() -> None:
    grid = act.grid()
    assert {a.structure for a in grid} == {s.id for s in act.STRUCTURES}
    assert set(act.BY_STRUCTURE) <= set(pl.TYPES)                       # the defined-risk set Plays supports
    assert len({a.key for a in grid}) == len(grid)
    assert act.parse_key(grid[5].key) == grid[5]
    assert len(act.version_hash()) == 16 and act.version_hash() == act.version_hash()
    assert act.EXIT_RULES[0].id == "hold"


def test_every_composed_candidate_is_a_fair_defined_risk_position(world: Any) -> None:
    session = world.days[-30]
    ctx = pl.Context(session, closing(session), 0.04, None, 0.0, None, {}, [])
    tables = [tb for u in pl.load(world.market, world.research, session) if (tb := pl.table(u, ctx)) is not None]
    assert tables
    seen = Counter()
    for tb in tables:
        for a in act.grid():
            legs = act.legs_for(tb, a)
            if legs is None:
                continue
            seen[a.structure] += 1
            row = ex.priced(tb, a.structure, legs, ctx, a.key, "grid", a)
            assert row is not None
            assert row.entry > 0 and row.max_loss > 0
            assert pl.structure_of([{"kind": tb.quotes[i].kind, "side": s, "strike": tb.quotes[i].strike}
                                    for i, s in legs]) == a.structure
            lo, hi = next((b[2], b[3]) for b in act.DTE_BUCKETS if b[0] == a.bucket)
            assert lo <= row.x["dte"] <= hi
            if a.structure in ("long_call", "long_put"):
                assert abs(abs(row.x["anchor_delta"]) - a.delta) <= act.DELTA_TOLERANCE + 1e-9
    assert set(seen) == {s.id for s in act.STRUCTURES}


def test_a_closing_build_records_every_candidate_once_with_picks_and_an_exploration_slice(world: Any) -> None:
    session = world.days[-30]
    pl.build(world.market, world.research, closing(session))            # Plays records the session first
    picks = world.research.conn.execute("SELECT COUNT(*) FROM stk_plays WHERE session=?", (session.toordinal(),)).fetchone()[0]
    out = ex.record_forward(world.market, world.research, session, closing(session))
    assert out["recorded"] and out["underlyings"] == len(SYMBOLS)
    rows = world.research.conn.execute("SELECT * FROM stk_learn_candidates").fetchall()
    cap = ex.cap_for(len(SYMBOLS))
    origins: Counter[str] = Counter()
    explore: Counter[str] = Counter()
    for r in rows:
        assert r["source"] == "forward" and r["actions_version"] == act.version_hash()
        g = ex.unpack(r["rows_zlib"])
        grid = [x for x in g["rows"] if x["key"]]
        assert len(grid) <= cap
        for x in g["rows"]:
            origins[x["origin"].split(":")[0].split("+")[0]] += 1
            if x["explore"]:
                explore[x["key"].split("|")[0]] += 1
            assert all(v is None or isinstance(v, float) for v in x["x"].values())
        assert set(g["stock"]) == set(ft.STOCK_NAMES) and set(g["market"]) == set(ft.MARKET_NAMES)
    assert sum(explore.values()) == ex.EXPLORE_PER_DAY
    assert max(explore.values()) - min(explore.values()) <= 1                 # stratified across the structures
    if picks:
        assert origins["plays"] + sum(1 for r in rows for x in ex.unpack(r["rows_zlib"])["rows"]
                                      if x["origin"].startswith("grid+plays")) >= 1
    again = ex.record_forward(world.market, world.research, session, closing(session) + timedelta(hours=1))
    assert again["recorded"] is False
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        world.research.conn.execute("UPDATE stk_learn_candidates SET candidates = 0")


def test_the_cap_falls_with_more_stocks_and_never_below_its_floor() -> None:
    assert ex.cap_for(5) == ex.PER_UNDERLYING
    assert ex.cap_for(1000) == ex.MIN_PER_UNDERLYING
    assert ex.cap_for(200) == ex.PER_DAY // 200
    picked = ex.stratified(list(range(30)), ["a"] * 20 + ["b"] * 10, 10, "seed")
    assert Counter("a" if i < 20 else "b" for i in picked) == {"a": 5, "b": 5}
    assert picked == ex.stratified(list(range(30)), ["a"] * 20 + ["b"] * 10, 10, "seed")


def test_no_feature_reads_anything_after_the_decision(world: Any) -> None:
    session = world.days[-30]
    ctx = pl.Context(session, closing(session), 0.04, None, 0.0, None, {}, [])
    us = {u.symbol: u for u in pl.load(world.market, world.research, session)}

    def everything() -> tuple[dict[str, Any], dict[str, Any]]:
        reader = ft.Reader(world.market.conn, world.research.conn, session)
        return ({s: ft.stock_features(reader, u, ctx.rate) for s, u in us.items()},
                ft.market_features(reader, us["SPY"], ctx.rate))

    before = everything()
    # Wild values dated on and after the decision session in every source the features read.
    later = [d for d in world.days if d >= session]
    for d in later:
        world.market.save_bars(d, [Bar(s, d, 9e5, 9e5, 9e5, 9e5, 9e9) for s in [*SYMBOLS, "S12"]], "future", closing(d))
        world.research.conn.execute("INSERT OR REPLACE INTO stk_score_history(asof, symbol, scores_json) VALUES (?,?,?)",
                                    (d.toordinal(), "CHEAP", json.dumps({"c": 99.0, "momentum": 99.0})))
        world.market.save_option_daily("CHEAP", d, closing(d), {"iv30": 9.0, "expiries": []})
    world.market.save_rates([RatePoint(series, d, 99.0) for d in later for series in
                             ("VIXCLS", "BAMLH0A0HYM2", "UST10Y", "UST3M")])
    world.market.save_short_interest([ShortInterest("CHEAP", session - timedelta(days=3), 9e9, 1.0, 1.0, 999.0)],
                                     closing(session))
    world.market.conn.execute("INSERT INTO stk_earnings_dates(symbol, report_date, hour, source, first_seen, last_seen) "
                              "VALUES ('CHEAP', ?, 'amc', 'test', ?, ?)",
                              ((session + timedelta(days=3)).toordinal(), closing(session).isoformat(),
                               closing(session).isoformat()))
    after = everything()
    assert after == before
    assert before[0]["CHEAP"]["ret_5d"] is not None and before[1]["ust10y"] == pytest.approx(4.3)


def test_labels_come_from_stored_closing_quotes_only_and_are_written_once(world: Any) -> None:
    session = world.days[-30]
    ex.record_forward(world.market, world.research, session, closing(session))
    prices = lb.ForwardPrices(world.market.conn, world.research.conn)
    early = cal.session_close(world.days[-29]) + timedelta(hours=1)       # before LABEL_AFTER: nothing is due
    assert lb.run(world.research.conn, world.market.conn, prices, early)["checkpoints"] == 0
    now = cal.session_close(world.days[-1]) + timedelta(hours=4)
    first = lb.run(world.research.conn, world.market.conn, prices, now)
    assert first["checkpoints"] > 0
    assert lb.run(world.research.conn, world.market.conn, prices, now)["checkpoints"] == 0     # idempotent
    rows = world.research.conn.execute("SELECT * FROM stk_learn_labels").fetchall()
    names = {r["checkpoint"] for r in rows}
    assert {"s1", "s5", "s10", "s21"} <= names and any(n.startswith("exp:") for n in names)
    for r in rows:
        assert r["day"] > session.toordinal() and r["labelled_at"] > closing(session).isoformat()
        for x in lb.unpack_labels(r["labels_zlib"]):
            values = x[2:4] if not r["checkpoint"].startswith("exp:") else [x[1], x[2], x[3], *x[6]]
            assert all(v is None or v >= -1.0 - 1e-9 for v in values)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        world.research.conn.execute("DELETE FROM stk_learn_labels")


def test_a_quote_from_before_the_last_hour_is_not_a_closing_quote(world: Any) -> None:
    session = world.days[-30]
    ex.record_forward(world.market, world.research, session, closing(session))
    day = world.days[-29]
    early = datetime.combine(day, datetime.min.time(), cal.NEW_YORK).replace(hour=11).astimezone(UTC).isoformat()
    world.market.conn.execute("UPDATE stk_option_chains SET observed_at=? WHERE day=?", (early, day.toordinal()))
    from desk.stocks.repo import _pack, _unpack

    for r in world.market.conn.execute("SELECT underlying, expiration, chain_zlib FROM stk_option_chains WHERE day=?",
                                       (day.toordinal(),)).fetchall():
        quotes = [[*q[:9], early, *q[10:]] for q in _unpack(r[2])]
        world.market.conn.execute("UPDATE stk_option_chains SET chain_zlib=? WHERE underlying=? AND day=? AND "
                                  "expiration=?", (_pack(quotes), r[0], day.toordinal(), r[1]))
    now = cal.session_close(world.days[-1]) + timedelta(hours=4)
    lb.run(world.research.conn, world.market.conn, lb.ForwardPrices(world.market.conn, world.research.conn), now)
    s1 = world.research.conn.execute("SELECT have, rows FROM stk_learn_labels WHERE checkpoint='s1'").fetchall()
    assert s1 and all(r["have"] == 0 for r in s1)                 # missing, never estimated


def test_exit_rules_on_a_path() -> None:
    path = [0.1, None, 0.6, -0.7, 0.2]
    got = dict(zip(act.EXIT_IDS, lb.exit_returns(path, -1.0), strict=True))
    assert got["hold"] == -1.0
    assert got["tp25"] == 0.6 and got["tp50"] == 0.6 and got["tp100"] == -1.0
    assert got["sl50"] == -0.7 and got["tp50_sl50"] == 0.6
    assert got["t5"] == 0.2 and got["t10"] == -1.0                 # expired before ten sessions: settled
    assert lb.exit_returns([0.1, None], 0.3)[act.EXIT_IDS.index("t5")] == 0.3
    assert lb.exit_returns([0.1] * 4 + [None], 0.3)[act.EXIT_IDS.index("t5")] is None   # its close is missing


def test_replay_examples_are_built_and_labelled_apart(tmp_path: Path, monkeypatch: Any) -> None:
    from desk.stocks import iv_history as ivh
    from desk.stocks import lotto_history as lh
    from tests.unit.test_stocks_play_replay import NOW, FakeMassive, download_all

    w = make_world(tmp_path, n=12)
    try:
        names = ["SPY", w.specs[0].symbol]
        for symbol in names:
            w.market.save_option_daily(symbol, w.days[-1], NOW, {"price": 100.0, "iv30": 0.3, "expiries": []})
        monthly, weekly = ivh.expiries, lh.expiries
        monkeypatch.setattr(ivh, "expiries", lambda today: monthly(today)[:3])
        monkeypatch.setattr(ivh, "names", lambda market, today, n=60: names)
        monkeypatch.setattr(lh, "expiries", lambda today: weekly(today)[:4])
        monkeypatch.setattr(lh, "NAMES", tuple(names))
        download_all(w, FakeMassive({s: {b.day: b.close for b in w.market.bars(s)} for s in names}))
        got = rp.step(w.market, w.research, NOW, days=50)
        assert got["days"] >= 4 and got["candidates"] > 0
        assert rp.step(w.market, w.research, NOW, days=50)["days"] == 0             # each day once
        rows = w.research.conn.execute("SELECT * FROM stk_learn_candidates").fetchall()
        assert {r["source"] for r in rows} == {"replay"} and {r["explore"] for r in rows} == {0}
        assert {r["price_source"] for r in rows} == {"massive_daily_bars"}
        done = lb.run(w.research.conn, w.market.conn, lb.ReplayPrices(w.market.conn), NOW)
        assert done["checkpoints"] > 0
        labels = w.research.conn.execute("SELECT * FROM stk_learn_labels").fetchall()
        assert {r["source"] for r in labels} == {"replay"} and sum(r["have"] for r in labels) > 0
        assert not w.research.conn.execute("SELECT 1 FROM stk_learn_candidates WHERE source='forward'").fetchone()
    finally:
        w.close()


def test_kestrel_check_covers_the_learner_tables(world: Any) -> None:
    from desk.stocks import accuracy as acc

    session = world.days[-30]
    ex.record_forward(world.market, world.research, session, closing(session))
    now = cal.session_close(world.days[-1]) + timedelta(hours=4)
    lb.run(world.research.conn, world.market.conn, lb.ForwardPrices(world.market.conn, world.research.conn), now)
    src = acc.Sources(world.paths, now, world.dbs.market, world.dbs.research, None, None, {})
    features, labels = acc.check_learner_features(src), acc.check_learner_labels(src)
    assert features.checked > 100 and features.violations == 0
    assert labels.checked > 5 and labels.violations == 0
    # A label below -100% of the money at risk, and one dated on its own decision day, are both found.
    group = world.research.conn.execute("SELECT * FROM stk_learn_labels LIMIT 1").fetchone()
    for name, day, value in (("s98", group["session"] + 1, -1.5), ("s99", group["session"], 0.1)):
        try:
            world.research.conn.execute(
                "INSERT INTO stk_learn_labels(source, session, symbol, actions_version, labels_version, checkpoint, day,"
                " rows, have, labels_zlib, labelled_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (group["source"], group["session"], group["symbol"], group["actions_version"], group["labels_version"],
                 name, day, 1, 1, lb._pack([[0, "quoted", value, value]]), now.isoformat()))
        except sqlite3.IntegrityError:          # the table itself refuses a label dated on its decision day
            assert day == group["session"]
    assert acc.check_learner_labels(src).violations == 1
