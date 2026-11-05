"""A recovered first sighting kept only its option quotes: its stock price is the nearest one stored at or before it
(the saved chain, else a tracked quote, else the latest published close), labelled, and never a later price."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from desk.core.paths import DeskPaths
from desk.stocks import calendar as cal
from desk.stocks import play_archive as archive
from desk.stocks import play_tracking as pt
from desk.stocks.contracts import Bar, OptionQuote, OptionTick
from desk.stocks.repo import StocksDatabases, StocksMarket

DAY = date(2026, 11, 4)
SEEN = datetime(2026, 11, 4, 17, 0, tzinfo=UTC)            # noon in New York


@pytest.fixture
def dbs(tmp_path: Path) -> Any:
    from tests.conftest import REPO

    out = StocksDatabases.open(DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env"))
    yield out
    out.close()


def _quote(symbol: str, expiration: date) -> OptionQuote:
    occ = f"{symbol}{expiration:%y%m%d}C00050000"
    return OptionQuote(occ, symbol, "call", 50.0, expiration, 1.0, 1.1, 1.05, 10, 100, None, SEEN)


def test_a_recovered_sightings_stock_price_is_the_nearest_stored_before_it(dbs: Any) -> None:
    market = StocksMarket(dbs.market)
    first, second = date(2026, 11, 20), date(2026, 12, 18)
    market.save_option_chain("AAA", DAY, first, datetime(2026, 11, 4, 15, tzinfo=UTC), 50.0, [_quote("AAA", first)])
    market.save_option_chain("AAA", DAY, second, datetime(2026, 11, 4, 20, 30, tzinfo=UTC), 52.0,
                             [_quote("AAA", second)])
    chain = archive.sighting_price(dbs.market, dbs.research, "AAA", [], SEEN)
    assert chain == {"price": 50.0, "source": "the saved option chain", "at": "2026-11-04T15:00:00+00:00"}
    # no chain that session: a tracked quote of one of its legs (a tick, or a folded intraday point)
    leg = {"occ": "BBB261218C00030000"}
    tick = OptionTick(leg["occ"], 1.0, 1.2, None, None, 1, 1, None, None, None, None, None, 30.0,
                      datetime(2026, 11, 4, 16, tzinfo=UTC))
    pt.save_ticks(dbs.research, pt.tick_rows([tick], tick.quoted_at or SEEN, DAY, "schwab"))
    dbs.research.execute("INSERT INTO stk_track_intraday(occ, session, status, points, packed, folded_at) VALUES "
                         "(?, ?, 'quoted', 2, ?, 'x')",
                         (leg["occ"], DAY.toordinal(), pt.pack([[600, 1.0, 1.1, 29.0, None], [780, 1.0, 1.1, 31.0, None]])))
    tracked = archive.sighting_price(dbs.market, dbs.research, "BBB", [leg], SEEN)
    assert tracked is not None and tracked["price"] == 30.0 and tracked["source"] == "a tracked quote"
    early = archive.sighting_price(dbs.market, dbs.research, "BBB", [leg], datetime(2026, 11, 4, 15, 30, tzinfo=UTC))
    assert early is not None and early["price"] == 29.0                     # the 10:00 point, never the later tick
    # neither: the latest daily close already published when it was seen (not that day's later close)
    for d, close in ((date(2026, 11, 3), 70.0), (DAY, 71.0)):
        market.save_bars(d, [Bar("CCC", d, close, close, close, close, 1e6)], "synthetic", SEEN)
    bar = archive.sighting_price(dbs.market, dbs.research, "CCC", [], SEEN)
    assert bar == {"price": 70.0, "source": "the daily close", "at": cal.session_close(date(2026, 11, 3)).isoformat()}
    assert archive.sighting_price(dbs.market, dbs.research, "DDD", [], SEEN) is None
    play = {"symbol": "AAA", "legs": [], "recovered_sighting": True, "underlying_price": None}
    filled = archive.with_sighting_price(dbs.market, dbs.research, play, SEEN.isoformat())
    assert filled["underlying_price"] == 50.0 and filled["underlying_source"] == "the saved option chain"
    assert archive.with_sighting_price(dbs.market, dbs.research, {**play, "recovered_sighting": False},
                                       SEEN.isoformat())["underlying_price"] is None   # only recovered sightings
