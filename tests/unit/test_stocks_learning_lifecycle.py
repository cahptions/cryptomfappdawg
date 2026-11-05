"""Lifecycle Learning: every expiry from weeklies to LEAPS, bought once and valued as it ages; minute-hold seeds
retired without erasing their record."""

import json
from datetime import UTC, date, datetime, time, timedelta

import pytest

from desk.core.errors import DeskError
from desk.stocks import learning
from desk.stocks import learning_retail as retail
from desk.stocks.contracts import OptionQuote
from desk.stocks.paper.book import PaperBook
from tests.stocks_world import make_world
from tests.unit.test_stocks_learning import AT

EXPIRIES = {"weekly": date(2026, 10, 30), "1m": date(2026, 11, 20), "3m": date(2027, 1, 15),
            "6m": date(2027, 4, 16), "leaps": date(2027, 12, 17)}


def ladder(world, at, bid=4.8, ask=5.0, source="synthetic_lifecycle"):
    """SPY calls and puts at 100 in every bucket's expiry."""
    for expiry in EXPIRIES.values():
        quotes = [OptionQuote(f"SPY{expiry:%y%m%d}{kind[0].upper()}00100000", "SPY", kind, 100, expiry, bid, ask,
                              (bid + ask) / 2, 50000, 2_000_000, None, at) for kind in ("call", "put")]
        world.market.save_option_chain("SPY", at.date(), expiry, at, 100, quotes, source=source)


def recipes(conn):
    return {json.loads(r["config_json"]).get("bucket"): r for r in conn.execute("SELECT * FROM stk_learning_experiments")}


def test_old_seeds_retire_once_and_every_bucket_is_seeded(tmp_path):
    world = make_world(tmp_path, n=12)
    conn = learning.open_book(world.paths)
    try:
        old = learning.add_experiment(conn, AT, "long_put", 50_000, 120)          # a minute-hold seed from before
        learning.initialize(conn, AT)
        assert learning.retired(conn) == {old}
        lifecycle = [json.loads(r["config_json"]) for r in conn.execute("SELECT config_json FROM stk_learning_experiments")
                     if json.loads(r["config_json"]).get("kind") == "lifecycle"]
        assert {(c["structure"], c["bucket"]) for c in lifecycle} == {
            (s, b) for s in learning.STRUCTURES for b in learning.BUCKETS}
        leaps = next(c for c in lifecycle if c["bucket"] == "leaps")
        assert leaps["checkpoints"] == [1, 7, 14, 30, 60, 90, 120, 180] and leaps["exit_days_left"] == 60
        later = learning.add_experiment(conn, AT, "long_call", 50_000, 30)       # registered after: active
        learning.initialize(conn, AT + timedelta(days=1))
        assert learning.retired(conn) == {old} and later not in learning.retired(conn)
    finally:
        conn.close()
        world.close()


def test_a_registered_recipe_chooses_its_holding_time_and_runs_beside_the_seeds(tmp_path):
    world = make_world(tmp_path, n=12)
    conn = learning.open_book(world.paths)
    try:
        learning.initialize(conn, AT)
        seeds = conn.execute("SELECT COUNT(*) FROM stk_learning_experiments").fetchone()[0]
        assert learning.add_lifecycle(conn, AT, "long_call", "1m") == learning.add_lifecycle(conn, AT, "long_call", "1m", 30)
        assert conn.execute("SELECT COUNT(*) FROM stk_learning_experiments").fetchone()[0] == seeds
        ident = learning.add_lifecycle(conn, AT, "long_call", "1m", 14)
        cfg = json.loads(conn.execute("SELECT config_json FROM stk_learning_experiments WHERE id=?", (ident,)).fetchone()[0])
        assert cfg["kind"] == "lifecycle" and cfg["hold_minutes"] == 14 * 1440 and cfg["checkpoints"] == [1, 7, 14]
        assert cfg["label"] == "About 1 month, held up to 14 days" and cfg["dte"] == [21, 45]
        assert "Holding-time variant" in cfg["change"]
        year = json.loads(conn.execute("SELECT config_json FROM stk_learning_experiments WHERE id=?",
                                       (learning.add_lifecycle(conn, AT, "long_put", "leaps", 365),)).fetchone()[0])
        assert year["checkpoints"][-3:] == [180, 270, 365]                     # long LEAPS holds valued to a year
        for structure, bucket, days in (("long_call", "weekly", 12), ("long_call", "1m", 0), ("short_call", "1m", 7),
                                        ("long_call", "2y", 7)):
            with pytest.raises(DeskError):
                learning.add_lifecycle(conn, AT, structure, bucket, days)
        assert ident not in learning.retired(conn)
        ladder(world, AT)
        learning.tick(conn, world.market, AT)
        assert conn.execute("SELECT COUNT(*) FROM stk_learning_intents WHERE experiment_id=?", (ident,)).fetchone()[0]
    finally:
        conn.close()
        world.close()


def test_every_bucket_buys_its_own_expiry_paced_and_never_a_retired_recipe(tmp_path):
    world = make_world(tmp_path, n=12)
    conn = learning.open_book(world.paths)
    try:
        old = learning.add_experiment(conn, AT, "long_call", 50_000, 30)
        ladder(world, AT)
        learning.tick(conn, world.market, AT)
        bought = {}
        for r in conn.execute("SELECT i.experiment_id, p.order_json FROM stk_pending p JOIN stk_learning_intents i "
                              "ON i.order_id=p.id WHERE p.account=?", (learning.RESEARCH,)):
            plan = json.loads(r["order_json"])
            bought.setdefault(plan.get("bucket"), set()).add(plan["legs"][0]["expiration"])
            assert r["experiment_id"] != old                                    # retired: nothing new
        assert {b: {date.fromisoformat(e) for e in v} for b, v in bought.items()} == {
            b: {e} for b, e in EXPIRIES.items()}
        # Two entries a day per recipe: later passes the same day place nothing more.
        for minutes in (1, 2, 3):
            ladder(world, AT + timedelta(minutes=minutes))
            learning.tick(conn, world.market, AT + timedelta(minutes=minutes))
        per_recipe = conn.execute("SELECT i.experiment_id, COUNT(*) n FROM stk_learning_intents i JOIN stk_pending p "
                                  "ON p.id=i.order_id WHERE p.account=? GROUP BY i.experiment_id",
                                  (learning.RESEARCH,)).fetchall()
        assert per_recipe and max(r["n"] for r in per_recipe) <= 2
        # Each recipe holds at most an equal share of the account.
        share = 500_000 / 15
        held = conn.execute("SELECT MAX(s) FROM (SELECT SUM(entry_cost_micros+entry_fees_micros)/1e6 s FROM "
                            "stk_positions WHERE account=? GROUP BY json_extract(plan_json,'$.experiment_id'))",
                            (learning.RESEARCH,)).fetchone()[0]
        assert held is not None and held <= share + 5_000
    finally:
        conn.close()
        world.close()


def test_retired_research_is_history_for_the_small_accounts_and_lifecycle_is_never_size_doubled(tmp_path):
    world = make_world(tmp_path, n=12)
    conn = learning.open_book(world.paths)
    try:
        old = learning.add_experiment(conn, AT - timedelta(days=40), "long_put", 50_000, 120)
        learning.initialize(conn, AT - timedelta(days=30))
        book = PaperBook(conn)
        cfg = conn.execute("SELECT * FROM stk_learning_experiments WHERE id=?", (old,)).fetchone()
        did = book.decide(AT, AT.date(), learning.RESEARCH, cfg["spec_sha256"], "SPY", "ENTER", ["Old seed"], {})
        pid = book.open_position(account=learning.RESEARCH, spec_sha256=cfg["spec_sha256"], asset="option",
                                 symbol="SPY", structure="long_put", legs=[], decision_id=did, at=AT, day=AT.date(),
                                 cost=100., fees=0., max_loss=100., stop_price=None, expires=date(2026, 11, 20),
                                 plan={"experiment_id": old, "hold_minutes": 120})
        position = next(p for p in book.positions(learning.RESEARCH) if p.id == pid)
        book.close_position(position, at=AT + timedelta(hours=2), day=AT.date(), credit=190., fees=0.,
                            reason="Old seed", evidence={})
        conn.execute("INSERT INTO stk_learning_outcomes VALUES (?,?,?,?,?,?,?,?,?,'sold at a quote')",
                     (pid, old, AT.isoformat(), (AT + timedelta(hours=2)).isoformat(), "2026-10-21:SPY", 0.9, 90., 1, 1))
        # A +90% retired put is not something the $1k/$5k accounts copy: they start from the 1-month call instead.
        policies = retail.policies(conn, AT + timedelta(days=1))
        assert [p["evidence"]["mode"] for p in policies] == ["starter"]
        assert policies[0]["config"]["bucket"] == "1m" and policies[0]["config"]["structure"] == "long_call"
    finally:
        conn.close()
        world.close()


def test_a_long_dated_position_is_valued_at_each_checkpoint_and_sold_at_its_horizon(tmp_path):
    world = make_world(tmp_path, n=12)
    conn = learning.open_book(world.paths)
    try:
        ladder(world, AT)
        learning.tick(conn, world.market, AT)
        ladder(world, AT + timedelta(minutes=1))
        learning.tick(conn, world.market, AT + timedelta(minutes=1))
        leaps = [p for p in PaperBook(conn).positions(learning.RESEARCH) if p.plan.get("bucket") == "leaps"]
        assert leaps
        for days, price in ((1, 5.2), (7, 5.5), (30, 6.5)):
            at = datetime.combine((AT + timedelta(days=days)).date(), time(16), UTC)   # 11 or 12 in New York
            while not learning.cal.is_trading_day(at.date()):
                at += timedelta(days=1)
            ladder(world, at, bid=price, ask=price + 0.2)
            learning.tick(conn, world.market, at)
        rows = conn.execute("SELECT days, net_return FROM stk_learning_checkpoints WHERE position_id=? ORDER BY days",
                            (leaps[0].id,)).fetchall()
        assert [r["days"] for r in rows] == [1, 7, 14, 30]            # 14 arrives with the 30-day quote
        assert rows[0]["net_return"] < rows[-1]["net_return"]        # holding longer paid here
        assert next(p for p in PaperBook(conn).positions(state=None) if p.id == leaps[0].id).plan["hold_minutes"] == \
            180 * 1440
        assert conn.execute("SELECT state FROM stk_positions WHERE id=?", (leaps[0].id,)).fetchone()[0] == "OPEN"
        horizon = datetime(2027, 4, 21, 15, tzinfo=UTC)               # 180 days on: the LEAPS recipe sells
        ladder(world, horizon, bid=7.0, ask=7.2)
        learning.tick(conn, world.market, horizon)
        state, reason = conn.execute("SELECT state, exit_reason FROM stk_positions WHERE id=?", (leaps[0].id,)).fetchone()
        assert state == "CLOSED" and reason.startswith("Registered holding horizon reached")
    finally:
        conn.close()
        world.close()
