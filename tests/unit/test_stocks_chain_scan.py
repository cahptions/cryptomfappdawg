"""The after-close chain scan (desk.stocks.chain_scan): who it reads, when, bounded passes that resume where they
stopped, outcomes remembered, and a clear skip without Schwab. A fake read-only Schwab client; no network."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from desk.core.errors import ProviderError, ProviderErrorKind
from desk.core.paths import DeskPaths
from desk.stocks import calendar as cal
from desk.stocks import chain_scan as cs
from desk.stocks.contracts import OptionQuote, StockQuote
from desk.stocks.repo import StocksDatabases, StocksMarket
from tests.conftest import REPO
from tests.unit.test_stocks_option_signals import chain

DAY = date(2026, 11, 16)                     # a Monday
AFTER = cal.session_close(DAY) + timedelta(hours=1)
SYMBOLS = [f"S{i:02d}" for i in range(12)]


class FakeSchwab:
    configured = True

    def __init__(self) -> None:
        self.asked: list[tuple[str, Any]] = []

    async def quote_details(self, symbols: list[str]) -> tuple[list[StockQuote], dict[str, dict[str, Any]]]:
        self.asked.append(("quotes", tuple(symbols)))
        return ([StockQuote(s, 100.0, 99.9, 100.1, 99.0, 3e6, AFTER) for s in symbols],
                {s: {"borrow_rate": 4.5, "hard_to_borrow": True} if s == "S01" else {"hard_to_borrow": False}
                 for s in symbols})

    async def chain_window(self, symbol: str, first: date, last: date, *, strikes: int | None
                           ) -> tuple[dict[date, list[OptionQuote]], float | None]:
        self.asked.append(("chain", symbol))
        assert (first - DAY).days == cs.WINDOW[0] and (last - DAY).days == cs.WINDOW[1] and strikes == cs.STRIKES
        if symbol == "S02":
            return {}, 100.0
        if symbol == "S03":
            raise ProviderError("schwab", ProviderErrorKind.HTTP_ERROR, "chains: HTTP 500", 500)
        return chain(DAY), 100.0


class Suite:
    def __init__(self, schwab: Any) -> None:
        self.schwab = schwab


@pytest.fixture
def market(tmp_path: Path) -> Iterator[StocksMarket]:
    dbs = StocksDatabases.open(DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env"))
    m = StocksMarket(dbs.market)
    seen = "2026-01-01T00:00:00+00:00"
    sessions = cal.trading_days(DAY - timedelta(days=40), DAY)
    for i, s in enumerate([*SYMBOLS, "PENNY", "GONE"]):
        m.conn.execute("INSERT INTO stk_listings VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (s, s, "NYSE", "common", 0, 0, None, 0 if s == "GONE" else 1, seen, seen))
        price = 2.0 if s == "PENNY" else 100.0
        m.conn.executemany("INSERT INTO stk_bars(symbol, day, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)",
                           [(s, d.toordinal(), price, price, price, price, 1e6 * (20 - i)) for d in sessions])
    try:
        yield m
    finally:
        dbs.close()


def test_the_window_is_after_the_close_until_the_next_open() -> None:
    assert cs.session_to_scan(cal.session_close(DAY) - timedelta(hours=3)) is None        # trading: nothing to scan
    assert cs.session_to_scan(cal.session_close(DAY) + timedelta(minutes=5)) is None      # quotes still settling
    assert cs.session_to_scan(AFTER) == DAY
    next_open = datetime.combine(cal.next_trading_day(DAY), datetime.min.time().replace(hour=9, minute=29),
                                 cal.NEW_YORK)
    assert cs.session_to_scan(next_open) == DAY
    assert cs.session_to_scan(next_open + timedelta(minutes=2)) is None            # the next session has opened


def test_the_most_liquid_listed_stocks_priced_above_five_dollars(market: StocksMarket) -> None:
    assert cs.eligible(market.conn, DAY) == SYMBOLS                 # by dollar volume; no penny stock, no delisting
    assert cs.eligible(market.conn, DAY, 3) == SYMBOLS[:3]


async def test_passes_are_bounded_resume_and_remember_outcomes(market: StocksMarket) -> None:
    client = FakeSchwab()
    first = await cs.scan(client, market, AFTER, max_requests=5)
    assert first["requests"] == 5 and first["stopped"] == "pass budget"
    assert [a for a in client.asked if a[0] == "chain"] == [("chain", s) for s in SYMBOLS[:4]]
    assert first["scanned"] == 2 and first["no_options"] == 1 and first["failed"] == 1 and first["remaining"] == 8
    assert cs.due(_with_job(market, first), AFTER)
    client.asked.clear()
    rest = await cs.scan(client, market, AFTER)
    chains = [a[1] for a in client.asked if a[0] == "chain"]
    assert chains == SYMBOLS[4:]                                   # resumed: nothing asked twice
    assert rest["remaining"] == 0 and rest["scanned"] == 10 and "stopped" not in rest
    assert not cs.due(_with_job(market, rest), AFTER)
    row = market.conn.execute("SELECT * FROM stk_option_signals WHERE symbol='S01'").fetchone()
    assert row["source"] == "scan" and row["borrow_rate"] == 4.5 and row["hard_to_borrow"] == 1
    assert row["stock_volume"] == 19e6 and row["option_volume"] > 0 and row["iv30"] == pytest.approx(0.30, abs=1e-3)
    assert market.conn.execute("SELECT hard_to_borrow FROM stk_option_signals WHERE symbol='S04'").fetchone()[0] == 0
    # a stock without listed options is left out for a month; a failure only for its session
    nxt = cal.next_trading_day(DAY)
    assert "S02" not in cs.eligible(market.conn, nxt) and "S03" in cs.eligible(market.conn, nxt)


async def test_a_pass_stops_at_its_time_budget(market: StocksMarket) -> None:
    ticks = iter(range(0, 10_000, 100))
    out = await cs.scan(FakeSchwab(), market, AFTER, max_seconds=250, clock=lambda: float(next(ticks)))
    assert out["stopped"] == "pass budget" and 0 < out["remaining"] < len(SYMBOLS)


async def test_without_schwab_it_is_skipped_with_the_reason(market: StocksMarket) -> None:
    class Off:
        configured = False

    for suite in (Suite(None), Suite(Off())):
        out = await cs.job(suite, market, AFTER)
        assert out["ok"] and out["skipped"] == cs.NOT_CONNECTED
    assert market.jobs()[cs.JOB]["last_error"] is None and cs.summary(market)["skipped"] == cs.NOT_CONNECTED
    assert not cs.due(market, AFTER)                                # skipped once a session, not every pass


async def test_an_expired_login_stops_the_pass_as_an_error(market: StocksMarket) -> None:
    class Expired(FakeSchwab):
        async def quote_details(self, symbols: list[str]) -> Any:
            raise ProviderError("schwab", ProviderErrorKind.HTTP_ERROR, "quotes: HTTP 401", 401)

    out = await cs.job(Suite(Expired()), market, AFTER)
    assert not out["ok"] and "401" in out["error"]


def _with_job(market: StocksMarket, detail: dict[str, Any]) -> StocksMarket:
    market.job(cs.JOB, AFTER, detail=detail)
    return market


async def test_schwab_reads_every_expiry_in_the_window_in_one_request() -> None:
    from desk.stocks.sources.schwab import SchwabClient
    from tests.unit.test_stocks_chain_coverage import TODAY, body

    days = [TODAY + timedelta(days=d) for d in (8, 15, 29, 92)]
    asked: list[dict[str, Any]] = []

    class Http:
        auth_headers = None

        async def request(self, endpoint: str, path: Any = None, params: Any = None) -> Any:
            asked.append({"endpoint": endpoint, **params})
            return type("R", (), {"status": 200, "body": body("AAA", days)})()

    class Tokens:
        configured = True
        headers = None

    client = SchwabClient(Http(), Tokens())                         # type: ignore[arg-type]
    chains, price = await client.chain_window("AAA", days[0], days[-1], strikes=cs.STRIKES)
    assert sorted(chains) == days and price == 100.0 and all(len(v) == 6 for v in chains.values())
    assert asked == [{"endpoint": "chains", "symbol": "AAA", "contractType": "ALL", "strategy": "SINGLE",
                      "fromDate": days[0].isoformat(), "toDate": days[-1].isoformat(),
                      "includeUnderlyingQuote": "true", "strikeCount": cs.STRIKES}]


def test_coverage_shows_in_data_health(tmp_path: Path) -> None:
    from desk.stocks.diagnostics import diagnose

    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env")
    dbs = StocksDatabases.open(paths)
    StocksMarket(dbs.market).job(cs.JOB, AFTER, detail={"session": DAY.isoformat(), "eligible": 500, "scanned": 480,
                                                        "thin": 5, "no_options": 3, "failed": 2, "remaining": 10})
    dbs.close()
    got = diagnose(paths)["option_scan"]
    assert got["eligible"] == 500 and got["scanned"] == 480 and got["failed"] == 2 and got["remaining"] == 10
    assert got["session"] == DAY.isoformat() and got["error_category"] is None
