"""Lottos sold at triple: the replay adds a daily-HIGH variant beside the close-based one (an upper bound, labelled),
and the forward PAPER picks take their take-profit during the session on fresh Schwab quotes."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from desk.stocks import calendar as cal
from desk.stocks import lotto_history as lh
from desk.stocks.contracts import OptionTick
from desk.stocks.paper.book import PaperBook
from desk.stocks.paper.fills import OPTION_FEE_PER_CONTRACT as FEE
from desk.stocks.paper.intraday import INTRADAY, take_profits
from desk.stocks.paper.strategies import BY_STRATEGY
from desk.storage.db import DbKind, connect, migrate


def test_a_daily_high_at_triple_sells_the_high_variant_but_not_the_close_one() -> None:
    d0 = date(2026, 10, 1)
    d1, d2 = cal.next_trading_day(d0), cal.next_trading_day(cal.next_trading_day(d0))
    expiry = d0 + timedelta(days=9)
    bars = {d0: {"open": 0.10, "high": 0.10, "low": 0.10, "close": 0.10},
            d1: {"open": 0.10, "high": 0.12, "low": 0.10, "close": 0.11},
            d2: {"open": 0.11, "high": 0.40, "low": 0.10, "close": 0.15}}       # spikes to 0.40, closes at 0.15
    high = lh.trade(bars, d0, expiry, "call", 50.0, 40.0, triple=True, on="high")
    close = lh.trade(bars, d0, expiry, "call", 50.0, 40.0, triple=True)
    assert high is not None and high["value"] == pytest.approx(3 * high["cost"]) and "day's high" in high["exit"]
    assert close is not None and close["exit"] == "expired worthless"
    limit = (3 * high["cost"] / high["units"] + FEE) / 100
    assert 0.12 < limit <= 0.40
    assert {"kestrel_high", "random_high"} <= {v for v, _ in lh.VARIANTS} and "not proof" in lh.METHOD


def _book() -> PaperBook:
    import sqlite3

    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    migrate(conn, DbKind.STOCKS_LEDGER)
    return PaperBook(conn)


OCC = "AMD261113C00300000"
NOW = datetime(2026, 11, 10, 16, 0, tzinfo=UTC)          # 11:00 in New York, a Tuesday


class Schwab:
    configured = True

    def __init__(self, bid: float, age: timedelta = timedelta(minutes=1)) -> None:
        self.bid, self.age, self.asked = bid, age, 0

    async def option_quotes(self, occs: list[str]) -> list[OptionTick]:
        self.asked += 1
        return [OptionTick(o, self.bid, self.bid + 0.02, None, None, 10, 100, None, None, None, None, None, None,
                           NOW - self.age) for o in occs]


def _hold(book: PaperBook, units: int = 10, price: float = 0.20) -> str:
    st = BY_STRATEGY["lotto_picks"]
    book.open_account(st.id, 50_000, NOW - timedelta(days=1), date(2026, 11, 9))
    did = book.decide(NOW - timedelta(days=1), date(2026, 11, 6), st.id, st.spec_sha256, "AMD", "ENTER", ["pick"], {})
    return book.open_position(account=st.id, spec_sha256=st.spec_sha256, asset="option", symbol="AMD",
                              structure="long_call", legs=[{"occ": OCC, "kind": "call", "strike": 300.0,
                                                            "expiration": "2026-11-13", "quantity": units,
                                                            "entry_price": price}],
                              decision_id=did, at=NOW - timedelta(hours=20), day=date(2026, 11, 9),
                              cost=units * price * 100, fees=units * FEE, max_loss=None, stop_price=None,
                              expires=date(2026, 11, 13), plan={"take_profit": 2.0})


def test_a_fresh_bid_worth_triple_sells_the_lotto_during_the_session() -> None:
    assert INTRADAY == ("lotto_picks",) and BY_STRATEGY["lotto_picks"].version == 2
    book = _book()
    _hold(book)
    not_yet = asyncio.run(take_profits(book, Schwab(0.55), NOW))           # +175%: not yet triple
    assert not_yet["sold"] == [] and book.positions("lotto_picks")
    stale = asyncio.run(take_profits(book, Schwab(0.70, timedelta(minutes=9)), NOW))
    assert stale["sold"] == [] and book.positions("lotto_picks")           # a quote older than five minutes
    got = asyncio.run(take_profits(book, Schwab(0.70), NOW))
    assert [s["account"] for s in got["sold"]] == ["lotto_picks"] and not book.positions("lotto_picks")
    [closed] = book.positions("lotto_picks", state="CLOSED")
    assert closed.exit_credit == pytest.approx(10 * 0.70 * 100) and closed.exit_fees == pytest.approx(10 * FEE)
    assert "intraday" in (closed.exit_reason or "") and "Schwab" in (closed.exit_reason or "")
    assert [d["action"] for d in book.decisions("lotto_picks")][:2] == ["EXIT", "ENTER"]
    assert not book.pending("lotto_picks")                                 # the order is recorded and filled


def test_no_quote_is_asked_when_the_market_is_closed_or_nothing_is_held() -> None:
    book = _book()
    schwab = Schwab(5.0)
    assert asyncio.run(take_profits(book, schwab, NOW))["requests"] == 0     # nothing held
    _hold(book)
    closed = datetime(2026, 11, 10, 23, 0, tzinfo=UTC)
    assert asyncio.run(take_profits(book, schwab, closed))["requests"] == 0 and schwab.asked == 0


def test_the_tracking_pass_runs_the_take_profits_on_the_ledger(tmp_path: Path) -> None:
    from desk.core.paths import DeskPaths
    from desk.stocks.play_tracking import paper_take_profits
    from desk.stocks.repo import LEDGER_FILE
    from tests.conftest import REPO

    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env")
    paths.db_dir.mkdir(parents=True, exist_ok=True)
    conn = connect(paths.db_dir / LEDGER_FILE, DbKind.STOCKS_LEDGER)
    migrate(conn, DbKind.STOCKS_LEDGER)
    _hold(PaperBook(conn))
    conn.close()
    got: dict[str, Any] = asyncio.run(paper_take_profits(paths, Schwab(0.80), NOW))
    assert len(got["sold"]) == 1
