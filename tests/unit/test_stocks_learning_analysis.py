"""`kestrel learning`: the research account against the reference on the same orders, read-only."""

from datetime import UTC, datetime, timedelta

import pytest

from desk.stocks import learning
from desk.stocks.learning_analysis import analyse, render
from desk.stocks.paper.book import PaperBook
from tests.stocks_world import make_world
from tests.unit.test_stocks_learning import AT, chain


def test_the_gap_to_the_reference_splits_into_shared_trades_and_size_it_could_not_afford(tmp_path):
    world = make_world(tmp_path, n=12)
    conn = learning.open_book(world.paths)
    try:
        chain(world, AT)
        learning.tick(conn, world.market, AT)                     # accounts open, orders placed
        # The research account as it was before the cap: topped up, so it can buy more than the reference, and a
        # $1,000,000-a-trade recipe the $500,000 reference cannot fully afford.
        PaperBook(conn).add_cash(learning.RESEARCH, AT, AT.date(), 1_500_000 * 1_000_000, "DEPOSIT")
        learning.add_experiment(conn, AT, "long_call", 1_000_000, 30)
        later = AT + timedelta(minutes=1)
        chain(world, later)
        assert learning.tick(conn, world.market, later)["filled"] > 0
        fill = later + timedelta(minutes=1)                       # the $1,000,000 orders fill on the next quote
        chain(world, fill)
        assert learning.tick(conn, world.market, fill)["filled"] > 0
        day_one = later + timedelta(days=1)
        chain(world, day_one, bid=5.6, ask=5.8)                   # a day later, worth more: the 1-day checkpoint
        learning.tick(conn, world.market, day_one)
        end = datetime(2026, 11, 17, 15, tzinfo=UTC)             # 3 days before expiry: sold, every holder made money
        chain(world, end, bid=5.8, ask=6.0)
        assert learning.tick(conn, world.market, end)["closed"] > 0
        before = [tuple(r) for r in conn.execute("SELECT * FROM stk_positions ORDER BY id")]
        a = analyse(conn, end)
        assert [tuple(r) for r in conn.execute("SELECT * FROM stk_positions ORDER BY id")] == before   # read-only
        books = {b["id"]: b for b in a["accounts"]}
        research, reference = books[learning.RESEARCH], books[learning.REFERENCE]
        assert research["contributed"] == 2_000_000 and reference["contributed"] == 500_000
        assert research["pnl"] > reference["pnl"] > 0
        pairs = a["pairs"]
        assert pairs["gap"] == pytest.approx(research["pnl"] - reference["pnl"], abs=0.05)
        assert pairs["buckets"]["smaller"]["intents"] + pairs["buckets"]["skipped"]["intents"] > 0
        assert any("About 1 month, held up to 30 days" in g["key"] for g in a["recipes"])
        assert any("$1,000,000 a trade, held 0.5 h" in g["key"] for g in a["recipes"])
        calls = next(r for r in a["lifecycle"] if r["structure"] == "long_call" and r["bucket"] == "1m")
        one_day = next(c for c in calls["checkpoints"] if c["days"] == 1)
        assert one_day["n"] == 1 and one_day["mean"] > 0 and calls["final"]["n"] == 1 and calls["open"] == 0
        assert "Does holding longer pay?" in render(a)
        assert a["funding"]["deposited"] == 2_000_000 and a["concentration"]["realized"] > 0
        text = render(a)
        assert "Research against the $500,000 reference, on the same orders:" in text
        assert "comes from size the reference could not afford" in text and "Research account by recipe" in text
        assert len(text.split("by recipe")[1].split("Concentration")[0].strip().splitlines()) - 1 == len(a["recipes"])
        assert "Research account by structure: long" in text and "By entry day: 2026-10-21" in text
        h = a["holding"]
        assert sum(v["trades"] for v in h["planned"].values()) == research["trades"]
        assert all(v["median_minutes"] >= 30 for v in h["planned"].values())       # never sold before its time
    finally:
        conn.close()
        world.close()


def test_an_empty_book_reports_without_failing(tmp_path):
    world = make_world(tmp_path, n=12)
    conn = learning.open_book(world.paths)
    try:
        text = render(analyse(conn, AT))
        assert "Books (like for like):" in text and "Per entry day: 0 days" in text
    finally:
        conn.close()
        world.close()
