"""Personal selections must enter from later fresh quotes, preserve history and never contaminate cohorts."""

import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from desk.core.errors import DeskError
from desk.stocks import calendar as cal
from desk.stocks import play_selections as ps
from desk.storage.db import DbKind, connect, migrate

OCC = "SPY261120P00100000"
START = datetime(2026, 10, 27, 8, tzinfo=cal.NEW_YORK)


def play():
    return {"symbol": "SPY", "type": "PUT", "action": "BUY", "structure": "long_put",
            "strike": 100, "expiration": "2026-11-20", "entry": 2., "source": "fixture",
            "underlying_price": 101., "max_loss": 200.65,
            "legs": [{"occ": OCC, "kind": "put", "strike": 100., "expiration": "2026-11-20",
                      "side": "buy", "bid": 1.9, "ask": 2., "iv": .3}]}


@pytest.fixture
def books(tmp_path):
    market = connect(tmp_path / "market.db", DbKind.STOCKS_MARKET)
    research = connect(tmp_path / "research.db", DbKind.STOCKS_RESEARCH)
    migrate(market, DbKind.STOCKS_MARKET)
    migrate(research, DbKind.STOCKS_RESEARCH)
    board = {"asof": "2026-10-26", "method": "fixture", "generated_at": START.isoformat(),
             "categories": [{"id": "puts", "plays": [play()]}]}
    ps.register(research, board, START)
    yield market, research, board["categories"][0]["plays"][0]["idea_id"]
    market.close()
    research.close()


def tick(research, at, *, quoted=None, bid=1.9, ask=2.):
    research.execute("INSERT INTO stk_track_ticks(occ,at,session,bid,ask,underlying,quoted_at,source) "
                     "VALUES (?,?,?,?,?,?,?,?)", (OCC, at.isoformat(), at.date().toordinal(), bid, ask, 101.,
                                                (quoted or at).isoformat(), "fixture"))


def select(research, ident, at=START):
    return ps.confirm(research, ident, 7, str(uuid.uuid4()), at)["id"]


@pytest.mark.parametrize("at,expected", [
    (START, "2026-10-27T09:30"),
    (START.replace(hour=10, minute=7), "2026-10-27T10:07"),
    (START.replace(hour=17), "2026-10-28T09:30"),
    (START.replace(day=30, hour=17), "2026-11-02T09:30"),
    (START.replace(month=12, day=25), "2026-12-28T09:30"),
])
def test_opening_default_and_after_open_confirmation_never_backdate(at, expected):
    assert ps.next_entry(at).isoformat().startswith(expected)


def test_confirmation_is_idempotent_and_append_only_and_survives_reopen(books):
    _market, research, ident = books
    request = str(uuid.uuid4())
    a = ps.confirm(research, ident, 7, request, START)
    b = ps.confirm(research, ident, 7, request, START + timedelta(seconds=5))
    assert a == b and ps.listing(research, START)["total"] == 1
    with pytest.raises(DeskError, match="different selection"):
        ps.confirm(research, ident, 8, request, START)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        research.execute("DELETE FROM stk_play_selections")
    path = research.execute("PRAGMA database_list").fetchone()[2]
    second = connect(Path(path), DbKind.STOCKS_RESEARCH)
    try:
        assert ps.ticket(second, a["id"], START)["quantity"] == 7
    finally:
        second.close()
    assert research.execute("SELECT COUNT(*) FROM stk_plays").fetchone()[0] == 0


@pytest.mark.parametrize("bad", ["premarket", "stale", "future", "missing_time", "crossed"])
def test_bad_quotes_never_create_opening_entries(books, bad):
    market, research, ident = books
    selected = select(research, ident)
    now = START.replace(hour=9, minute=31)
    stamp = now - timedelta(minutes=1)
    quoted = {"premarket": START, "stale": now-timedelta(minutes=3), "future": now+timedelta(seconds=1)}.get(bad, stamp)
    tick(research, stamp, quoted=quoted, bid=3. if bad == "crossed" else 1.9)
    if bad == "missing_time":
        research.execute("UPDATE stk_track_ticks SET quoted_at=NULL")
    assert ps.advance(market, research, now) == 0
    assert ps.ticket(research, selected, now)["state"] == "queued"


def test_fresh_entry_close_and_profit_path_use_actual_reference_quotes_not_idea_price(books):
    market, research, ident = books
    selected = select(research, ident)
    opening = START.replace(hour=9, minute=30)
    tick(research, opening, ask=3., bid=2.9)
    assert ps.advance(market, research, opening) == 1
    current = ps.ticket(research, selected, opening)
    assert current["entry"]["price"] == 3. and datetime.fromisoformat(current["entry"]["at"]) == opening
    later = opening + timedelta(hours=1)
    ps.action(research, selected, "close", later)
    assert ps.advance(market, research, later) == 0  # cannot close using an earlier quote
    tick(research, later + timedelta(seconds=5), bid=5., ask=5.1)
    assert ps.advance(market, research, later+timedelta(seconds=5)) == 1
    out = ps.view(research, selected, later+timedelta(minutes=10), personal=True)
    assert out["tracking"]["state"] == "closed"
    assert out["chart"]["contract_quantity"] == 7
    assert out["chart"]["bases"]["close"] == 3.
    assert out["chart"]["exits"]["close"]["best"]["pnl"] == pytest.approx(198.7)
    assert research.execute("SELECT COUNT(*) FROM stk_plays").fetchone()[0] == 0


def test_cancelled_and_expired_without_entry_never_fill_later(books):
    market, research, ident = books
    selected = select(research, ident)
    ps.action(research, selected, "cancel", START)
    tick(research, START.replace(hour=10))
    assert ps.advance(market, research, START.replace(hour=10)) == 0
    assert ps.ticket(research, selected, START)["state"] == "cancelled"
    pending = select(research, ident)
    after = START.replace(month=11, day=23)
    assert ps.advance(market, research, after) == 1
    assert ps.ticket(research, pending, after)["state"] == "cancelled"
    with pytest.raises(DeskError, match="expires"):
        select(research, ident, after)


def test_frozen_calculator_preserves_exact_put_when_chain_is_gone_and_rejects_wrong_stock(books):
    market, research, ident = books
    chain = ps.calculator_chain(market, research, ident, "SPY", datetime(2026,11,20).date())
    assert chain["contracts"][0]["occ"] == OCC
    assert chain["contracts"][0]["kind"] == "put"
    assert chain["asof"] == "2026-10-26" and chain["underlying_price"] == 101.
    with pytest.raises(DeskError, match="different stock"):
        ps.calculator_chain(market, research, ident, "QQQ", datetime(2026,11,20).date())
