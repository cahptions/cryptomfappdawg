"""The small accounts sell a lifecycle recipe's position at its best research checkpoint so far, using only checkpoints
valued before each decision, instead of copying the recipe's full horizon; and they copy a family that keeps its own
expiry window."""

import json
from datetime import timedelta

import pytest

from desk.core.clock import to_iso
from desk.stocks import learning as lab
from desk.stocks import learning_retail as retail
from desk.stocks.paper.book import PaperBook
from tests.stocks_world import make_world
from tests.unit.test_stocks_learning import AT, EXP, chain


def recipe(conn, structure="long_call", bucket="1m"):
    return next(r["id"] for r in conn.execute("SELECT id, config_json FROM stk_learning_experiments")
                if (json.loads(r["config_json"]).get("structure"), json.loads(r["config_json"]).get("bucket"))
                == (structure, bucket))


def checkpoints(conn, experiment, returns, at, n=12):
    """``n`` finished research positions of ``experiment``, each valued at ``returns`` (days → net return) at ``at``."""
    book = PaperBook(conn)
    for i in range(n):
        did = book.decide(at, at.date(), lab.RESEARCH, "synthetic", "SPY", "ENTER", ["Synthetic checkpoint fixture"], {})
        pid = book.open_position(account=lab.RESEARCH, spec_sha256="synthetic", asset="option", symbol="SPY",
                                 structure="long_call", legs=[], decision_id=did, at=at - timedelta(days=40, minutes=i),
                                 day=at.date(), cost=100., fees=0., max_loss=100., stop_price=None, expires=EXP,
                                 plan={"experiment_id": experiment})
        position = next(p for p in book.positions(lab.RESEARCH) if p.id == pid)
        book.close_position(position, at=at, day=at.date(), credit=100., fees=0., reason="Synthetic", evidence={})
        for days, net in returns.items():
            conn.execute("INSERT INTO stk_learning_checkpoints VALUES (?,?,?,?,?,?)",
                         (pid, days, to_iso(at), 100 * (1 + net), net, 100.))


def test_the_best_prior_checkpoint_sets_the_exit_and_later_ones_cannot(tmp_path):
    world = make_world(tmp_path, n=12)
    conn = lab.open_book(world.paths)
    try:
        lab.initialize(conn, AT - timedelta(days=60))
        call = recipe(conn)
        assert retail.policies(conn, AT)[0]["evidence"]["exit_days"] == 30          # nothing valued yet: full horizon
        checkpoints(conn, call, {1: -.05, 7: .2, 14: .05}, AT - timedelta(days=2))
        checkpoints(conn, call, {30: .9}, AT + timedelta(hours=1))                  # valued after the decision
        checkpoints(conn, call, {21: 2.0}, AT - timedelta(days=1), n=9)             # too few positions to name it
        starter = retail.policies(conn, AT)[0]
        assert starter["evidence"]["mode"] == "starter"
        assert starter["evidence"]["exit_days"] == 7
        assert starter["evidence"]["best_checkpoint"]["positions"] == 12
        assert "Best research checkpoint so far: 7 days" in starter["evidence"]["exit_basis"]
        later = retail.policies(conn, AT + timedelta(hours=2))[0]["evidence"]       # once valued, it can count
        assert later["exit_days"] == 30 and later["policy_hash"] != starter["evidence"]["policy_hash"]
        old = retail.policies(conn, AT + timedelta(days=95))[0]["evidence"]         # outside the 90-day window
        assert old["exit_days"] == 30 and old["best_checkpoint"] is None
    finally:
        conn.close()
        world.close()


def test_a_small_account_sells_at_the_best_checkpoint_while_research_holds_to_its_horizon(tmp_path):
    world = make_world(tmp_path, n=12)
    conn = lab.open_book(world.paths)
    try:
        lab.initialize(conn, AT - timedelta(days=60))
        checkpoints(conn, recipe(conn), {1: -.05, 7: .2, 14: .05}, AT - timedelta(days=2))
        chain(world, AT)
        lab.tick(conn, world.market, AT)
        state = lab.state(conn, "retail:" + lab.SMALL_ACCOUNTS[0])
        assert state["exit_days"] == 7 and "sells after 7 days" in state["note"]
        later = AT + timedelta(minutes=1)
        chain(world, later)
        lab.tick(conn, world.market, later)
        book = PaperBook(conn)
        small = book.positions(lab.SMALL_ACCOUNTS[0])
        assert small and all(p.plan["hold_minutes"] == 7 * 1440 and p.plan["recipe_hold_minutes"] == 30 * 1440
                             for p in small)
        week = AT + timedelta(days=7, minutes=5)
        chain(world, week, bid=5.5, ask=5.7)
        lab.tick(conn, world.market, week)
        closed = {p.id: p for p in book.positions(state="CLOSED")}
        assert all(p.id in closed and closed[p.id].exit_reason.startswith("Registered holding horizon reached")
                   for p in small)
        research = [p for p in book.positions(lab.RESEARCH) if p.plan.get("bucket") == "1m"]
        assert research and all(p.plan["hold_minutes"] == 30 * 1440 for p in research)   # research keeps its horizon
    finally:
        conn.close()
        world.close()


def finished(conn, experiment, net, entered, n=3):
    """``n`` matured research outcomes of ``experiment``, one stock each (separate underlying/day blocks)."""
    book = PaperBook(conn)
    for i in range(n):
        symbol = f"S{i}"
        did = book.decide(entered, entered.date(), lab.RESEARCH, "synthetic", symbol, "ENTER", ["Synthetic"], {})
        pid = book.open_position(account=lab.RESEARCH, spec_sha256="synthetic", asset="option", symbol=symbol,
                                 structure="long_call", legs=[], decision_id=did, at=entered, day=entered.date(),
                                 cost=100., fees=0., max_loss=100., stop_price=None, expires=EXP,
                                 plan={"experiment_id": experiment})
        position = next(p for p in book.positions(lab.RESEARCH) if p.id == pid)
        mature = entered + timedelta(days=1)
        book.close_position(position, at=mature, day=mature.date(), credit=100 * (1 + net), fees=0., reason="Synthetic",
                            evidence={})
        conn.execute("INSERT INTO stk_learning_outcomes VALUES (?,?,?,?,?,?,?,?,?,'sold at a quote')",
                     (pid, experiment, to_iso(entered), to_iso(mature), f"{entered.date().isoformat()}:{symbol}", net,
                      100 * net, 1, 1))


def test_lifecycle_families_keep_their_own_expiry_window_even_at_the_same_holding_time(tmp_path):
    world = make_world(tmp_path, n=12)
    conn = lab.open_book(world.paths)
    try:
        lab.initialize(conn, AT - timedelta(days=60))
        month = recipe(conn)                                                    # 1-month calls, held up to 30 days
        quarter = lab.add_lifecycle(conn, AT - timedelta(days=60), "long_call", "3m", 30)   # 3-month calls, 30 days
        finished(conn, month, .3, AT - timedelta(days=10))
        finished(conn, quarter, -.2, AT - timedelta(days=9))
        chosen = retail.policies(conn, AT)
        assert [(p["config"]["bucket"], p["evidence"]["experiment_ids"]) for p in chosen] == [("1m", [month])]
        assert chosen[0]["config"]["dte"] == [21, 45] and chosen[0]["evidence"]["blocks"] == 3
        families = {(f["bucket"], f["hold_minutes"]): f for f in lab.evidence_progress(conn, AT)["families"]}
        assert families[("1m", 30 * 1440)]["mean_net_return"] == pytest.approx(.3)
        assert families[("3m", 30 * 1440)]["mean_net_return"] == pytest.approx(-.2)
        # Spacing is per family: the 1-month family's last entry does not hold back the 3-month one.
        cfg = json.loads(conn.execute("SELECT config_json FROM stk_learning_experiments WHERE id=?",
                                      (quarter,)).fetchone()[0])
        assert retail.spacing_key("a", cfg, "SPY") == "retail_entry:a:long_call:43200:3m:SPY"
    finally:
        conn.close()
        world.close()
