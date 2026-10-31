"""`kestrel learning`: the research account against the reference on the same orders, read-only."""

from datetime import timedelta

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
        # The research account as it was before the cap: topped up, so it can buy more than the reference.
        PaperBook(conn).add_cash(learning.RESEARCH, AT, AT.date(), 1_500_000 * 1_000_000, "DEPOSIT")
        later = AT + timedelta(minutes=1)
        chain(world, later)
        assert learning.tick(conn, world.market, later)["filled"] > 0
        end = later + timedelta(minutes=121)
        chain(world, end, bid=5.8, ask=6.0)                      # the options rose: every holder made money
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
        assert a["recipes"] and all(" a trade, held " in g["key"] for g in a["recipes"])
        assert a["funding"]["deposited"] == 2_000_000 and a["concentration"]["realized"] > 0
        text = render(a)
        assert "Research against the $500,000 reference, on the same orders:" in text
        assert "comes from size the reference could not afford" in text and "Research account by recipe" in text
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
