"""Learning quotes every held and pending leg contract by contract each minute (Schwab /quotes, 100 a request), so
fills and exits no longer wait for the chain rotation; the rotation keeps serving new entries."""

from datetime import date, datetime, timedelta
from types import SimpleNamespace

from desk.core.config import load_config
from desk.core.errors import ProviderError, ProviderErrorKind
from desk.stocks import learning, learning_service
from desk.stocks.contracts import OptionTick
from desk.stocks.paper.book import PaperBook
from tests.stocks_world import make_world
from tests.unit.test_stocks_learning import AT, EXP, chain


def tick(occ, at, bid=4.8, ask=5.0):
    return OptionTick(occ, bid, ask, (bid + ask) / 2, None, 100, 2_000_000, None, None, None, None, None, 100.0, at)


class Schwab:
    configured = True

    def __init__(self, at, fail=False):
        self.at, self.fail, self.batches = at, fail, []

    async def option_quotes(self, occs):
        self.batches.append(list(occs))
        if self.fail:
            raise ProviderError("schwab", ProviderErrorKind.HTTP_ERROR, "quotes: HTTP 500", 500)
        return [tick(o, self.at) for o in occs]


def suite(schwab, chains):
    class Provider:
        configured = True

        async def quotes(self, symbols):
            return [SimpleNamespace(symbol=s, last=100, quoted_at=schwab.at) for s in symbols]

        async def expirations(self, symbol):
            return [EXP]

        async def chain(self, symbol, expiry):
            chains.append(symbol)
            return []

    class Suite:
        options = Provider()
        option_source = "schwab"

        async def aclose(self):
            pass

    Suite.schwab = schwab
    return Suite()


def test_a_per_contract_quote_has_the_chain_shape():
    q = learning_service.leg_quote(tick("SPY261120P00102500", AT))
    assert (q.underlying, q.kind, q.strike, q.expiration, q.open_interest) == ("SPY", "put", 102.5, EXP, 2_000_000)


async def test_held_and_pending_legs_are_quoted_first_and_their_quotes_fill_orders(tmp_path, monkeypatch):
    world = make_world(tmp_path, n=12)
    conn = learning.open_book(world.paths)
    later = AT + timedelta(minutes=1)
    schwab, chains = Schwab(later), []
    monkeypatch.setattr(learning_service, "build_stocks_suite", lambda *args: suite(schwab, chains))
    monkeypatch.setattr(learning_service, "utc_now", lambda: later)
    try:
        chain(world, AT)
        assert learning.tick(conn, world.market, AT)["orders"] > 0                 # pending on the AT chain
        result = await learning_service.collect(world.paths, load_config(world.paths.config_dir), conn, world.market,
                                                later)
        pending = {"SPY261120C00100000", "SPY261120P00100000"}
        assert schwab.batches == [sorted(pending)]                                   # one request, before any chain
        assert result["held_legs"] == 2 and result["held_legs_quoted"] == 2 and result["leg_requests"] == 1
        assert result["checked"] == ["SPY", "QQQ"] and chains == ["SPY", "QQQ"]     # rotation: new entries
        saved = conn.execute("SELECT observed_at FROM stk_learning_sources WHERE underlying=?",
                             (learning.HELD_LEGS,)).fetchall()
        assert len(saved) == 1
        # No newer chain arrived: only the per-contract quotes are later than the orders, and they fill them.
        progress = learning.tick(conn, world.market, later)
        assert progress["filled"] > 0 and progress["held_leg_quotes"] == 2
        assert all(datetime.fromisoformat(p.marks["at"]) == later for p in PaperBook(conn).positions())
        # Two minutes on, the saved quotes are stale and are not reused.
        assert learning.leg_quotes(conn, later + timedelta(seconds=121)) == {}
        assert learning.leg_quotes(conn, later - timedelta(seconds=1)) == {}        # never seen before it was saved
    finally:
        conn.close()
        world.close()


async def test_a_failed_quote_request_leaves_the_legs_to_the_chain_rotation(tmp_path, monkeypatch):
    world = make_world(tmp_path, n=12)
    conn = learning.open_book(world.paths)
    later = AT + timedelta(minutes=1)
    schwab, chains = Schwab(later, fail=True), []
    monkeypatch.setattr(learning_service, "build_stocks_suite", lambda *args: suite(schwab, chains))
    monkeypatch.setattr(learning_service, "utc_now", lambda: later)
    try:
        chain(world, AT)
        learning.tick(conn, world.market, AT)
        result = await learning_service.collect(world.paths, load_config(world.paths.config_dir), conn, world.market,
                                                later)
        assert result["held_legs_quoted"] == 0 and any("Schwab option quotes failed" in e for e in result["errors"])
        assert result["checked"][0] == "SPY"                                         # held symbol goes first again
        assert learning.state(conn, "leg_quotes") is None
    finally:
        conn.close()
        world.close()


async def test_a_large_book_is_quoted_100_contracts_a_request_and_rotates_past_the_minute_budget(tmp_path, monkeypatch):
    world = make_world(tmp_path, n=12)
    conn = learning.open_book(world.paths)
    monkeypatch.setattr(learning_service, "LEG_REQUESTS", 2)
    monkeypatch.setattr(learning_service, "utc_now", lambda: AT)
    try:
        occs = [f"SPY{date(2026, 11, 20):%y%m%d}C{strike * 1000:08d}" for strike in range(1, 251)]
        schwab = Schwab(AT)
        first = await learning_service.quote_legs(conn, suite(schwab, []), occs)
        assert [len(b) for b in schwab.batches] == [100, 100] and first["requests"] == 2
        second = await learning_service.quote_legs(conn, suite(schwab, []), occs)
        assert [len(b) for b in schwab.batches[2:]] == [50, 100]                     # page 3, then page 1 again
        assert len(first["quoted"] | second["quoted"]) == 250
    finally:
        conn.close()
        world.close()
