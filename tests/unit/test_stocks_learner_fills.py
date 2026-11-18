"""The Learner's fill model (desk.stocks.learner.fills; docs/STOCKS_LEARNER.md → Costs: the expected fill): a limit
fills only when a later saved quote of the same session crossed it; the estimate is shrunk toward no fill, monotone in
the fraction, and as of a session it uses nothing later. Synthetic quote paths only."""

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from desk.stocks import calendar as cal
from desk.stocks import plays as pl
from desk.stocks.learner import experience as ex
from desk.stocks.learner import fills as fl
from desk.stocks.play_marks import profit
from desk.stocks.play_tracking import pack
from tests.stocks_world import add_chains, make_world


def test_a_fill_is_observed_only_when_a_later_quote_crosses_the_limit() -> None:
    # bid 1.00 / ask 1.20: mid 1.10, half spread 0.10. Limits for a buy: 1.10, 1.125, 1.15, 1.175.
    still = [[570, 1.0, 1.2], [600, 1.0, 1.2], [700, 1.0, 1.2]]
    spread, buy, sell = fl.observe(still) or (0, [], [])
    assert spread == pytest.approx(0.2 / 1.1)
    assert buy == [0, 0, 0, 0] and sell == [0, 0, 0, 0]          # an unmoved market fills nothing inside the spread
    dipped = [[570, 1.0, 1.2], [640, 0.98, 1.15], [700, 1.05, 1.25]]
    _, buy, sell = fl.observe(dipped) or (0, [], [])
    assert buy == [0, 0, 1, 1]                                    # the ask touched 1.15: f = 0.5 and 0.75 fill
    assert sell == [0, 0, 1, 1]                                   # the bid reached 1.05 = mid − 0.5 × half
    # the anchor's own quote never counts: a crossing before or at the anchor is not a fill
    assert fl.observe([[570, 1.05, 1.1], [600, 1.0, 1.2]]) is not None
    _, buy, _ = fl.observe([[570, 1.0, 1.2], [575, 1.0, 1.2]]) or (0, [], [])
    assert buy == [0, 0, 0, 0]
    assert fl.observe([[570, 1.0, 1.2]]) is None                  # no later quote: nothing observed
    assert fl.observe([[570, 1.0, 1.0], [600, 0.9, 1.0]]) is None # no spread to improve on


def _obs(n: int, fill: float, seed: int, spread: float = 0.04) -> list[list[Any]]:
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        hit = rng.random() < fill
        out.append([f"X{i}", spread, 2000, 300, 30, 0.01, [0, int(hit), int(hit), 1], [0, 0, int(hit), 1]])
    return out


def test_no_data_means_paying_the_far_side_and_the_estimate_is_shrunk_and_monotone() -> None:
    empty = fl.Fills.from_rows({})
    t = empty.at(800_000)
    assert t.n == 0 and (t.p[:, :-1] == 0).all() and (t.p[:, -1] == 1).all()
    far = fl.structure_fill(t, [[1.1, 0.1, 5000, 500, 30, 0.0]], 120.0)
    assert far.fraction == 1.0 and far.chance == 1.0 and far.saved() == 0.0     # pay the ask
    few = fl.Fills.from_rows({739_000: _obs(5, 1.0, 1)}).at()
    many = fl.Fills.from_rows({739_000: _obs(2000, 0.7, 2)}).at()
    p_few, p_many = few.chance(0.04, 2000, 300, 30, 0.01), many.chance(0.04, 2000, 300, 30, 0.01)
    assert p_few[2] < 0.75                                        # 10 of 10 filled, pulled hard toward no fill
    assert p_many[2] == pytest.approx(0.7, abs=0.03) and p_many[1] == pytest.approx(0.35, abs=0.03)
    for p in (p_few, p_many):
        assert (np.diff(p) >= -1e-12).all() and p[-1] == 1.0      # never more likely nearer the mid
    # a cell with no observations borrows its spread bin's rate, not a guess of its own
    other = many.chance(0.04, 50, 0, 200, 0.2)
    assert 0 < other[2] <= p_many[2] + 1e-9


def test_the_estimate_as_of_a_session_uses_nothing_after_it() -> None:
    early = {739_000: _obs(300, 0.2, 3), 739_001: _obs(300, 0.2, 4)}
    later = {**early, 739_005: _obs(3000, 0.95, 5), 739_009: _obs(3000, 0.95, 6)}
    a = fl.Fills.from_rows(early).at(739_002)
    b = fl.Fills.from_rows(later).at(739_002)
    assert np.array_equal(a.p, b.p) and a.n == b.n == 1200
    assert fl.Fills.from_rows(later).at(738_000).n == 0           # before any observation: nothing known
    assert fl.Fills.from_rows(later).at(739_009).n > a.n


def test_a_structure_fills_when_every_leg_would_and_tries_a_limit_only_when_worth_it() -> None:
    table = fl.Fills.from_rows({739_000: _obs(4000, 0.9, 7)}).at()
    leg = [1.1, 0.1, 2000, 300, 30, 0.01]
    one = fl.structure_fill(table, [leg], 110.0)
    two = fl.structure_fill(table, [leg, leg], 110.0)
    assert two.p[2] == pytest.approx(one.p[2] ** 2, rel=1e-4)
    assert one.best < len(fl.FRACTIONS) - 1 and one.saving == pytest.approx(0.1)
    assert one.saved() == pytest.approx((1 - one.fraction) * 0.1)
    rare = fl.Fills.from_rows({739_000: [[f"X{i}", 0.04, 2000, 300, 30, 0.01, [0, 0, 0, int(i % 50 == 0)],
                                          [0, 0, 0, int(i % 50 == 0)]] for i in range(4000)]}).at()
    assert fl.structure_fill(rare, [leg], 110.0).fraction == 1.0  # a limit that rarely fills is not worth a missed trade


@pytest.mark.parametrize(("action", "entry", "max_loss", "value", "settled"), [
    ("BUY", 1.20, 121.3, 1.80, False), ("BUY", 1.20, 121.3, 0.0, True), ("BUY", 1.20, 121.3, 0.004, False),
    ("SELL", 0.80, 421.3, -0.30, False), ("SELL", 0.80, 421.3, -6.0, False), ("SELL", 0.80, 421.3, -5.0, True)])
def test_a_return_restated_at_a_better_entry_matches_plays_arithmetic(action: str, entry: float, max_loss: float,
                                                                      value: float, settled: bool) -> None:
    legs, saved = 2, 0.07
    _, ret = profit(action, entry, max_loss, value, legs, settled=settled)
    better = entry - saved if action == "BUY" else entry + saved
    _, want = profit(action, better, max_loss - (saved * 100 if action == "SELL" else 0), value, legs, settled=settled)
    assert fl.at_fill(ret, action, entry, max_loss, legs, saved) == pytest.approx(want, abs=1e-9)
    assert fl.at_fill(ret, action, entry, max_loss, legs, 0.0) == ret
    fill = fl.Fill((0.5, 0.6, 0.7, 0.8, 1.0), 0.1, 2)
    assert fl.net(ret, action, entry, max_loss, legs, fill) == pytest.approx(
        0.7 * fl.at_fill(ret, action, entry, max_loss, legs, 0.05))


def test_saved_quotes_become_observations_once_and_decision_legs_are_recorded(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=14)
    try:
        add_chains(w, ["SPY", "CHEAP", "S10"], w.days[-6:], max_days=90)
        session = w.days[-3]
        conn = w.research.conn
        conn.execute("INSERT INTO stk_track_intraday VALUES (?,?,?,?,?,?)",
                     ("CHEAP261218C00010000", session.toordinal(), "quoted", 3,
                      pack([[570, 1.0, 1.2, 10.5, 0.4], [640, 0.98, 1.15, 10.4, 0.4], [700, 1.0, 1.2, 10.5, 0.4]]),
                      "2026-11-02T22:00:00+00:00"))
        conn.execute("INSERT INTO stk_track_days(occ, session, status, bid, ask, volume, open_interest, source, recorded_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?)", ("CHEAP261218C00010000", cal.previous_trading_day(session).toordinal(),
                                                  "quoted", 1.0, 1.2, 40, 900, "schwab", "2026-11-01T22:00:00+00:00"))
        evening = cal.session_close(session) + timedelta(hours=3)
        out = fl.collect(conn, evening)
        assert out == {"sessions": 1, "observations": 1}
        assert fl.collect(conn, evening) == {"sessions": 0, "observations": 0}
        fills = fl.Fills.load(conn)
        (row,) = fills_rows = fl._unpack(conn.execute("SELECT obs_zlib FROM stk_learn_fill_obs").fetchone()[0])
        assert len(fills_rows) == 1 and row[2] == 900 and row[3] == 40 and row[6] == [0, 0, 1, 1]
        assert fills.observations() == 2 and fills.at(session.toordinal() - 1).n == 0
        # decision legs: each forward candidate's legs at its own session's stored quotes, matching its entry
        close = cal.session_close(session) + timedelta(minutes=30)
        pl.build(w.market, w.research, close)
        ex.record_forward(w.market, w.research, session, close)
        got = fl.record_entries(conn, w.market.conn, evening)
        assert got["groups"] > 0 and got["candidates_with_legs"] > 0
        assert fl.record_entries(conn, w.market.conn, evening)["groups"] == 0
        symbol = conn.execute("SELECT symbol FROM stk_learn_entry WHERE have > 0").fetchone()[0]
        legs = fl.entry_legs(conn, "forward", session.toordinal(), symbol)
        g = ex.unpack(conn.execute("SELECT rows_zlib FROM stk_learn_candidates WHERE symbol=? AND session=?",
                                   (symbol, session.toordinal())).fetchone()[0])
        i, facts = next(iter(legs.items()))
        assert len(facts) == len(g["rows"][i]["legs"])
        mid, half, oi, volume, dte, _ = facts[0]
        assert mid > 0 and half > 0 and oi == 5000 and volume == 800 and dte > 0
    finally:
        w.close()


def test_the_entry_must_match_the_stored_chain() -> None:
    row = {"legs": [["CHEAP261218C00010000", 1]], "action": "BUY", "entry": 1.20}
    chain = {"CHEAP261218C00010000": (1.0, 1.2, 900, 40)}
    assert fl.decision_legs(row, chain, date(2026, 11, 2), 10.0) is not None
    assert fl.decision_legs({**row, "entry": 1.25}, chain, date(2026, 11, 2), 10.0) is None
    assert fl.decision_legs(row, {}, date(2026, 11, 2), 10.0) is None
