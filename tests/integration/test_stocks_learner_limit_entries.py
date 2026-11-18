"""The Learner's entries at the fill model's limit (learner.policy v2): the limit comes from the fill model as of the
decision; it fills at its limits only on an observed crossing, is refused when saved quotes show none, and otherwise
by the seeded draw recorded in the decision; the random twin is treated the same. Synthetic chains only."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import numpy as np
import pytest

from desk.core.config import load_config
from desk.stocks import calendar as cal
from desk.stocks import plays as pl
from desk.stocks.learner import experience as ex
from desk.stocks.learner import fills as fl
from desk.stocks.learner import policy
from desk.stocks.paper.book import PaperBook
from desk.stocks.paper.engine import PaperEngine
from tests.conftest import REPO
from tests.stocks_world import add_chains, make_world

SETTINGS = load_config(REPO / "config").settings.stocks
SYMBOLS = ["SPY", "CHEAP", "SQZ", "S10", "S11", "S12"]


def closing(d: date) -> datetime:
    return cal.session_close(d) + timedelta(minutes=30)


def eager(p: float = 0.9) -> fl.Table:
    """A fill model whose limits inside the spread fill with chance ``p`` (synthetic)."""
    table = np.ones((fl.CELLS, len(fl.FRACTIONS)))
    table[:, :-1] = p
    return fl.Table(table, 500, None)


def test_the_fill_decision_is_observed_first_then_the_recorded_draw() -> None:
    buy, sell = {"A": (1, 1.00)}, {"B": (-1, 2.00)}
    # a stored quote crossed every leg: filled, observed (whatever the draw says)
    assert fl.limit_fill(buy, {"A": (0.9, 0.99)}, {}, 0.1, 0.99) == (
        True, "observed: a stored quote of the session crossed the limit")
    assert fl.limit_fill(sell, {}, {"B": [(1.9, 2.1), (2.0, 2.2)]}, 0.0, 0.99)[0]
    # every leg has saved intraday quotes and none crossed: not filled, observed
    got = fl.limit_fill({**buy, **sell}, {"A": (0.9, 1.0), "B": (1.8, 2.1)},
                        {"A": [(0.95, 1.05)], "B": [(1.8, 1.95)]}, 0.99, 0.0)
    assert got == (False, "observed: no saved quote of the session crossed the limit")
    # no intraday quotes and no crossing at the snapshot: the fill model's chance against the recorded draw
    assert fl.limit_fill(buy, {"A": (1.0, 1.1)}, {}, 0.6, 0.59)[0]
    filled, why = fl.limit_fill(buy, {"A": (1.0, 1.1)}, {}, 0.6, 0.61)
    assert not filled and why == "fill model: 60% chance, seeded draw 0.610 (not filled)"


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> Any:
    w = make_world(tmp_path_factory.mktemp("limits"), n=14)
    add_chains(w, SYMBOLS, w.days[-5:], max_days=120)
    for d in w.days[-5:-1]:
        pl.build(w.market, w.research, closing(d))
        ex.record_forward(w.market, w.research, d, closing(d))
    try:
        yield w
    finally:
        w.close()


def chosen(w: Any, session: date, n: int = 4) -> tuple[list[policy.Choice], policy.Pricer]:
    pricer = policy.Pricer(w.market, session)
    out: list[policy.Choice] = []
    for o in policy.session_candidates(w.research.conn, session):
        if o.symbol in {c.option.symbol for c in out}:
            continue
        o.legs = pricer(o)
        if o.legs is None:
            continue
        o.scores = {"expected": 0.1}
        out.append(policy.Choice(o, "exploit", 1, o.risk_unit, "Exploit: test", []))
        if len(out) == n:
            break
    return out, pricer


def test_no_fill_observations_pay_the_far_side_as_before(world: Any) -> None:
    session = world.days[-4]
    (c, *_), pricer = chosen(world, session, 1)
    entry = policy.entry_fill(c.option, policy.fill_table(world.research.conn, session), pricer, session, "learner")
    assert entry["fraction"] == 1.0 and entry["limits"] is None and entry["chance"] == 1.0


def test_a_limit_comes_from_the_decision_quotes_and_the_twin_is_treated_the_same(world: Any) -> None:
    session = world.days[-4]
    choices, pricer = chosen(world, session, 1)
    o = choices[0].option
    mine = policy.entry_fill(o, eager(), pricer, session, policy.ACCOUNT)
    twin = policy.entry_fill(o, eager(), pricer, session, policy.RANDOM)
    assert mine["fraction"] < 1 and mine["limits"]
    assert {k: v for k, v in mine.items() if k != "draw"} == {k: v for k, v in twin.items() if k != "draw"}
    assert mine["draw"] != twin["draw"]                                           # each its own seeded draw
    assert mine == policy.entry_fill(o, eager(), pricer, session, policy.ACCOUNT)   # reproducible
    quotes = pricer.quotes[o.symbol]
    for occ, sign in o.row["legs"]:
        q = quotes[occ]
        mid, half = (q.bid + q.ask) / 2, (q.ask - q.bid) / 2
        want = mid + mine["fraction"] * half if sign > 0 else mid - mine["fraction"] * half
        assert abs(mine["limits"][occ] - want) <= 0.01 and q.bid - 1e-9 <= mine["limits"][occ] <= q.ask + 1e-9


def test_limit_entries_fill_at_their_limits_or_are_recorded_as_not_filled(world: Any, monkeypatch: Any) -> None:
    session, fill_day = world.days[-3], world.days[-2]
    book = PaperBook(world.dbs.ledger)
    monkeypatch.setattr(policy, "fill_table", lambda research, s: eager())
    learner, pricer = chosen(world, session)
    twins, _ = chosen(world, session)
    with book.atomic():
        for c in learner:
            policy._place(book, policy.LEARNER, session, closing(session), c, None, policy.CAPITAL, "uptrend",
                          eager(), pricer)
        for c in twins:
            c = policy.Choice(c.option, "random", c.units, c.risk, "Random baseline: test", [])
            policy._place(book, policy.LEARNER_RANDOM, session, closing(session), c, None, policy.CAPITAL,
                          "uptrend", eager(), pricer)
    orders = [o for a in policy.ACCOUNTS for o in book.pending(a)]
    assert orders and all(o.order["limit_entry"]["limits"] for o in orders)
    for r in world.dbs.ledger.execute("SELECT scores_json FROM stk_learner_decisions WHERE session=?",
                                      (session.toordinal(),)):
        fill = json.loads(r[0])["fill"]
        assert 0 <= fill["draw"] < 1 and 0 < fill["chance"] < 1                  # the draw is in the decision
    # One leg of the first order has saved intraday quotes that never crossed and the snapshot does not either:
    # observed, not filled. Everything else is decided by the snapshot or the recorded draw.
    def quoted(o: Any) -> dict[str, Any]:
        _, chains = world.market.option_chain(o.order["symbol"], fill_day)
        return {q.occ: q for qs in chains.values() for q in qs}

    first = next(o for o in orders if all(leg["occ"] in quoted(o) for leg in o.order["legs"]))
    blocked = {leg["occ"] for leg in first.order["legs"]}
    snap = quoted(first)

    def intraday(occ: str, day: date) -> list[tuple[float, float]]:
        if occ not in blocked or day != fill_day:
            return []
        leg = next(x for x in first.order["legs"] if x["occ"] == occ)
        q = snap[occ]
        return [(q.bid, leg["limit"] + 1.0)] if leg["quantity"] > 0 else [(leg["limit"] - 1.0, q.ask)]

    real = fl.limit_fill

    def no_snapshot_cross(limits: Any, snapshot: Any, saved: Any, chance: float, draw: float) -> tuple[bool, str]:
        quiet = {occ: snapshot[occ] for occ in snapshot if occ not in blocked}
        return real(limits, quiet, saved, chance, draw)

    monkeypatch.setattr(fl, "limit_fill", no_snapshot_cross)
    engine = PaperEngine(world.market, book, SETTINGS, policy.STRATEGIES, intraday=intraday)
    rep = engine.step(fill_day, datetime.combine(fill_day, time(22, 30), UTC), None)
    assert rep.skipped is None
    ids = {o.id for o in orders}
    resolved = {r["id"]: r for r in world.dbs.ledger.execute("SELECT * FROM stk_pending") if r["id"] in ids}
    assert resolved[first.id]["state"] == "CANCELLED"
    assert "did not fill (observed: no saved quote of the session crossed the limit)" in resolved[first.id]["note"]
    opened = {p.decision_id: p for a in policy.ACCOUNTS for p in book.positions(a)}
    states = {"FILLED": 0, "CANCELLED": 0, "PENDING": 0}
    for o in orders:
        if o.id == first.id:
            continue
        r = resolved[o.id]
        le = o.order["limit_entry"]
        states[r["state"]] += 1
        if r["state"] == "PENDING":                      # a leg not on the fill session's chain: it waits, as before
            assert not all(leg["occ"] in quoted(o) for leg in o.order["legs"])
        elif r["state"] == "FILLED":
            p = opened[o.decision_id]
            assert {leg["occ"]: leg["entry_price"] for leg in p.legs} == le["limits"]          # at its limits
            assert p.plan["entry_fill"]["basis"].startswith(("observed", "fill model"))
            assert p.entry_cost + p.entry_fees <= o.order["max_debit"] + 1e-6
        else:
            assert r["state"] == "CANCELLED" and "did not fill" in r["note"]
            if {leg["occ"] for leg in o.order["legs"]} <= blocked:       # the twin of the same trade: same quotes
                assert "observed: no saved quote" in r["note"]
            else:
                assert "seeded draw" in r["note"] and le["draw"] >= le["chance"]
    assert states["FILLED"] >= 1
    # kestrel check: a limit fill is at a price that existed (inside the decision's quote) and within its limits.
    from desk.stocks import accuracy as acc

    src = acc.Sources(world.paths, closing(fill_day), world.market.conn, world.research.conn, world.dbs.ledger)
    for name in ("check_book_fills", "check_book_cash", "check_book_positions"):
        result = getattr(acc, name)(src)
        assert result.violations == 0, (name, result.examples)
