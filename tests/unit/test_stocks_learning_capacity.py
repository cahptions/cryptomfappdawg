"""Capacity is the size displayed at the side traded (ask to buy, bid to sell), capped by the open-interest rule; there
is no depth beyond the top of the book."""

import json
import zlib
from dataclasses import replace
from datetime import timedelta

from desk.stocks import learning
from desk.stocks.paper.book import PaperBook
from desk.stocks.sources.schwab import parse_chain, parse_option_quotes
from tests.stocks_world import make_world
from tests.unit.test_schwab_sources import EXP as SCHWAB_EXP
from tests.unit.test_schwab_sources import chain as schwab_chain
from tests.unit.test_schwab_sources import contract
from tests.unit.test_stocks_learning import AT, EXP, chain
from tests.unit.test_stocks_play_tracking import option_row


def test_schwab_chain_rows_and_quotes_carry_the_displayed_sizes():
    sized = parse_chain(schwab_chain(contract(bidSize=12, askSize=7)), "SPY", SCHWAB_EXP)[0]
    assert (sized.bid_size, sized.ask_size) == (12, 7)
    bare = parse_chain(schwab_chain(contract()), "SPY", SCHWAB_EXP)[0]
    assert (bare.bid_size, bare.ask_size) == (None, None)                     # missing stays missing, never zero
    occ = "SPY261120C00500000"
    tick = parse_option_quotes(json.dumps({occ: option_row(occ, bidSize=3, askSize=40)}).encode(), {occ})[0]
    assert (tick.bid_size, tick.ask_size) == (3, 40)
    tick = parse_option_quotes(json.dumps({occ: option_row(occ, bidSize="n/a")}).encode(), {occ})[0]
    assert (tick.bid_size, tick.ask_size) == (None, None)


def test_saved_chains_keep_sizes_only_when_reported(tmp_path):
    world = make_world(tmp_path, n=12)
    try:
        quotes = chain(world, AT)
        world.market.save_option_chain("SPY", AT.date(), EXP, AT, 100,
                                       [replace(quotes[0], bid_size=5, ask_size=2), quotes[1]], source="synthetic_learning")
        _, chains = world.market.option_chain("SPY", AT.date())
        got = {q.kind: q for q in chains[EXP]}
        assert (got["call"].bid_size, got["call"].ask_size) == (5, 2)
        assert (got["put"].bid_size, got["put"].ask_size) == (None, None)
        raw = json.loads(zlib.decompress(world.market.conn.execute(
            "SELECT chain_zlib FROM stk_option_chains WHERE underlying='SPY'").fetchone()[0]))
        assert sorted(len(r) for r in raw) == [10, 12]                       # other readers index 0-9: unchanged
    finally:
        world.close()


def test_capacity_is_the_displayed_size_at_the_side_traded_capped_by_open_interest(tmp_path):
    world = make_world(tmp_path, n=12)
    conn = learning.open_book(world.paths)
    try:
        q = chain(world, AT)[0]                                              # open interest 2,000,000: rule = 2,000
        assert learning.capacity(conn, learning.RESEARCH, [q]) == 2000       # no size reported: the rule alone
        sized = replace(q, bid_size=9, ask_size=3)
        assert learning.capacity(conn, learning.RESEARCH, [sized]) == 3
        assert learning.capacity(conn, learning.RESEARCH, [sized], opening=False) == 9
        assert learning.capacity(conn, learning.RESEARCH, [replace(q, ask_size=0)]) == 0      # nobody offering
        assert learning.capacity(conn, learning.RESEARCH, [replace(q, open_interest=1000, ask_size=50)]) == 1
        learning.consume(conn, learning.RESEARCH, [sized], 2)
        assert learning.capacity(conn, learning.RESEARCH, [sized]) == 1      # shared on the same quote timestamp
        assert learning.capacity(conn, learning.REFERENCE, [sized]) == 3     # independent books
    finally:
        conn.close()
        world.close()


def test_a_fill_takes_no_more_than_the_ask_size_and_records_it(tmp_path):
    world = make_world(tmp_path, n=12)
    conn = learning.open_book(world.paths)
    try:
        chain(world, AT)
        learning.tick(conn, world.market, AT)
        later = AT + timedelta(minutes=1)
        quotes = [replace(q, quoted_at=later, bid_size=4, ask_size=2) for q in chain(world, later)]
        world.market.save_option_chain("SPY", later.date(), EXP, later, 100, quotes, source="synthetic_learning")
        assert learning.tick(conn, world.market, later)["filled"] > 0
        for account in (learning.RESEARCH, learning.REFERENCE):
            positions = PaperBook(conn).positions(account)
            call_contracts = sum(leg["quantity"] for p in positions for leg in p.legs if leg["kind"] == "call")
            assert 0 < call_contracts <= 2                                   # shared by the call and straddle orders
            assert all("Size displayed at the ask" in p.plan["capacity_basis"] for p in positions)
        events = [json.loads(r[0]) for r in conn.execute("SELECT payload_json FROM stk_events WHERE kind='EXPERIMENT_FILL'")]
        assert events and all(e["quoted_size"] and set(e["quoted_size"]) == {2} and not e["capacity_estimated"]
                              for e in events)
        assert conn.execute("SELECT COUNT(*) FROM stk_pending WHERE state='PENDING'").fetchone()[0] > 0   # remainder waits
    finally:
        conn.close()
        world.close()
