"""Learning settles a position still held at expiry at intrinsic value on the official close, like the ordinary PAPER
books: a real outcome and a training label, never a stale mark left open."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from desk.core.clock import to_iso
from desk.stocks import learning
from desk.stocks.contracts import Bar, Split
from desk.stocks.paper.book import PaperBook
from tests.stocks_world import make_world
from tests.unit.test_stocks_learning import AT
from tests.unit.test_stocks_learning_lifecycle import EXPIRIES, ladder

WEEKLY = EXPIRIES["weekly"]                                   # Friday 2026-10-30
AFTER_CLOSE = datetime(2026, 10, 30, 21, 0, tzinfo=UTC)       # 5 PM in New York
SATURDAY = datetime(2026, 10, 31, 15, 0, tzinfo=UTC)


def bought(world, conn):
    """Every bucket bought at AT and filled a minute later; then no quote ever arrives again."""
    ladder(world, AT)
    learning.tick(conn, world.market, AT)
    ladder(world, AT + timedelta(minutes=1))
    learning.tick(conn, world.market, AT + timedelta(minutes=1))
    weekly = [p for p in PaperBook(conn).positions() if p.expires == WEEKLY]
    assert {p.structure for p in weekly} == set(learning.STRUCTURES)
    assert {p.account for p in weekly} >= {learning.RESEARCH, learning.REFERENCE}
    return weekly


def close(world, price):
    world.market.save_bars(WEEKLY, [Bar("SPY", WEEKLY, 101, 104, 100, price, 1e8)], "synthetic", AFTER_CLOSE)


def test_an_expired_position_settles_at_intrinsic_on_the_official_close(tmp_path):
    world = make_world(tmp_path, n=12)
    conn = learning.open_book(world.paths)
    try:
        weekly = bought(world, conn)
        close(world, 103.0)
        # Before that day's close the contract is still trading: nothing settles, even with a bar stored early.
        learning.tick(conn, world.market, datetime(2026, 10, 30, 18, 0, tzinfo=UTC))
        assert all(p.state == "OPEN" for p in PaperBook(conn).positions() if p.expires == WEEKLY)
        result = learning.tick(conn, world.market, SATURDAY)            # market closed: settlement still runs
        assert result["settled"] == len(weekly)
        book = PaperBook(conn)
        closed = {p.id: p for p in book.positions(state="CLOSED")}
        for p in weekly:
            done = closed[p.id]
            units = p.plan["filled_units"]
            if p.structure == "long_put":                              # worthless: no value, no fee
                assert done.exit_credit == 0 and done.exit_fees == 0
                assert done.exit_reason.endswith("(worthless)")
            else:                                                      # the call leg is $3 in the money
                assert done.exit_credit == pytest.approx(300 * units)
                assert done.exit_fees == pytest.approx(0.65 * units)  # the worthless put leg of a straddle pays none
            assert "settled at intrinsic on the $103.00 official close" in done.exit_reason
        assert conn.execute("SELECT COUNT(*) FROM stk_cash WHERE reason='EXPIRE'").fetchone()[0] == len(weekly)
        research = [p for p in weekly if p.account == learning.RESEARCH]
        rows = conn.execute("SELECT * FROM stk_learning_outcomes WHERE basis=?", (learning.SETTLED,)).fetchall()
        assert {r["position_id"] for r in rows} == {p.id for p in research}   # labels from research only
        for r in rows:
            p = next(x for x in research if x.id == r["position_id"])
            assert r["matured_at"] == to_iso(SATURDAY)                    # when known, never back-dated
            assert r["net_return"] == pytest.approx(r["pnl"] / (p.entry_cost + p.entry_fees))
        exits = [json.loads(r["payload_json"]) for r in conn.execute("SELECT payload_json FROM stk_events "
                                                                      "WHERE kind='EXPIRY_SETTLEMENT'")]
        assert len(exits) == len(weekly) and all(e["underlying_close"] == 103.0 for e in exits)
        # Longer-dated positions are untouched; the report counts the settlements and no unresolved expiry.
        assert all(p.expires > WEEKLY for p in book.positions())
        report = learning.report(conn, SATURDAY)
        main = next(a for a in report["accounts"] if a["id"] == learning.RESEARCH)
        assert main["unresolved_expiries"] == 0 and main["expiry_settlements"] == len(research)
        assert report["evidence"]["settled_at_expiry"] == len(research)
        weekly_recipes = [r for r in report["lifecycle"] if r["bucket"] == "weekly"]
        assert sum(r["final"]["settled"] for r in weekly_recipes) == len(research)
        assert "settles at intrinsic value" in report["execution_note"]
        # A NAV observation records the settled equity even though the market was closed.
        assert conn.execute("SELECT COUNT(*) FROM stk_learning_nav WHERE at=?", (to_iso(SATURDAY),)).fetchone()[0]
        assert learning.tick(conn, world.market, SATURDAY + timedelta(hours=1))["settled"] == 0   # once only
    finally:
        conn.close()
        world.close()


def test_without_the_official_close_an_expired_position_stays_visibly_unresolved(tmp_path):
    world = make_world(tmp_path, n=12)
    conn = learning.open_book(world.paths)
    try:
        weekly = bought(world, conn)
        assert learning.tick(conn, world.market, SATURDAY)["settled"] == 0
        assert learning.report(conn, SATURDAY)["evidence"]["unresolved_expiries"] == len(
            [p for p in weekly if p.account == learning.RESEARCH])
        close(world, 97.0)                                              # the close arrives later
        later = SATURDAY + timedelta(days=1)
        assert learning.tick(conn, world.market, later)["settled"] == len(weekly)
        closed = {p.id: p for p in PaperBook(conn).positions(state="CLOSED")}
        for p in weekly:
            units = p.plan["filled_units"]
            expected = 0 if p.structure == "long_call" else 300 * units   # now the put leg is in the money
            assert closed[p.id].exit_credit == pytest.approx(expected)
        assert learning.report(conn, later)["evidence"]["unresolved_expiries"] == 0
    finally:
        conn.close()
        world.close()


def test_a_split_during_the_hold_is_not_settled_on_the_unadjusted_strike(tmp_path):
    world = make_world(tmp_path, n=12)
    conn = learning.open_book(world.paths)
    try:
        weekly = bought(world, conn)
        close(world, 103.0)
        world.market.save_splits([Split("SPY", WEEKLY - timedelta(days=3), 1, 2)], AFTER_CLOSE)
        assert learning.tick(conn, world.market, SATURDAY)["settled"] == 0
        assert all(p.state == "OPEN" for p in PaperBook(conn).positions() if p.id in {w.id for w in weekly})
    finally:
        conn.close()
        world.close()
