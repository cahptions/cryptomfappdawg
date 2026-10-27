"""Plays history on the synthetic market across several sessions: picks are recorded with twins, tracked live at the
close, folded, marked every session, estimated across a laptop-off session and settled at expiry — and nothing a
later session learns changes an earlier mark."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from desk.core.config import load_config
from desk.stocks import calendar as cal
from desk.stocks import play_marks as pm
from desk.stocks import play_tracking as pt
from desk.stocks import plays as pl
from desk.stocks.analysis import Analyzer
from desk.stocks.contracts import OptionTick
from desk.stocks.daily import refresh
from desk.stocks.prices import load_panel
from desk.stocks.repo import StocksMarket, day_of
from tests.conftest import REPO
from tests.stocks_world import ASOF, NOW, StocksWorld, add_chains, make_world

SETTINGS = load_config(REPO / "config").settings.stocks
SYMBOLS = ["CHEAP", "SQZ", "S10", "S11", "S12", "PRICEY", "TRAP", "S13", "S14"]


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), cal.NEW_YORK).astimezone(UTC)


class ChainQuotes:
    """A Schwab stand-in that quotes whatever the stored chain of the day lists, with Schwab-style greeks."""

    configured = True

    def __init__(self, market: StocksMarket) -> None:
        self.market, self.day = market, ASOF
        self.requests = 0

    async def option_quotes(self, occs: list[str]) -> list[OptionTick]:
        self.requests += 1
        chains = self.market.chains_on(self.day)
        by = {q.occ: (q, item["price"]) for item in chains.values() for qs in item["expirations"].values() for q in qs}
        return [OptionTick(o, by[o][0].bid, by[o][0].ask, by[o][0].last, None, by[o][0].volume,
                           by[o][0].open_interest, 0.4, 0.5, 0.01, -0.02, 0.1, by[o][1], at(self.day, 15, 55))
                for o in occs if o in by]


def short_dated(w: StocksWorld, session: date) -> None:
    """The synthetic chains are too dear for Lottos, so record one at-the-money call expiring two sessions later,
    from a contract the stored chain really lists, to follow a pick all the way to its settlement."""
    chains = w.market.chains_on(session)["CHEAP"]
    expiration = min(chains["expirations"])
    q = min((q for q in chains["expirations"][expiration] if q.kind == "call"), key=lambda q: abs(q.strike - chains["price"]))
    leg = {"occ": q.occ, "kind": "call", "side": "buy", "strike": q.strike, "expiration": expiration.isoformat(),
           "bid": q.bid, "ask": q.ask, "mid": q.mid, "iv": 0.4, "delta": 0.5}
    play = {"symbol": "CHEAP", "rank": 99, "action": "BUY", "structure": "long_call", "expiration": expiration.isoformat(),
            "event": "itm", "event_kind": "call", "event_strike": q.strike, "event_width": 0.0, "entry": q.ask,
            "max_loss": (q.ask or 0) * 100 + 0.65, "legs": [leg], "underlying_price": chains["price"],
            "itm_market": 0.5, "itm_history": 0.5, "itm_kestrel": 0.5, "itm_kestrel_raw": 0.5, "profit_kestrel": 0.4}
    w.research.conn.execute(
        "INSERT INTO stk_plays(session, category, rank, symbol, action, structure, expiration, event, event_kind,"
        " event_strike, event_width, entry, max_loss, legs_json, underlying, itm_market, itm_history, itm_kestrel,"
        " itm_kestrel_raw, profit_kestrel, play_json, method, recorded_at) VALUES (?, 'lottos', 99, 'CHEAP', 'BUY',"
        " 'long_call', ?, 'itm', 'call', ?, 0, ?, ?, ?, ?, 0.5, 0.5, 0.5, 0.5, 0.4, ?, ?, ?)",
        (day_of(session), day_of(expiration), q.strike, q.ask, play["max_loss"], json.dumps([leg]), chains["price"],
         json.dumps(play), pl.method(), at(session, 16, 20).isoformat()))


@pytest.fixture(scope="module")
def history(tmp_path_factory: pytest.TempPathFactory) -> Any:
    w = make_world(tmp_path_factory.mktemp("history"))
    sessions = w.days[-6:]
    add_chains(w, SYMBOLS, w.days[-16:-6])
    refresh(Analyzer(w.market, w.research, SETTINGS), load_panel(w.market, w.days[0], ASOF), ASOF, NOW)
    quotes = ChainQuotes(w.market)

    def rate(d: date) -> float:
        return pl._rate(w.market, d)

    snapshots: dict[date, dict[tuple[Any, ...], tuple[Any, ...]]] = {}
    off = sessions[3]                                                    # the laptop was off this whole session
    for s in sessions:
        add_chains(w, SYMBOLS, [s])
        quotes.day = s
        if s != off:
            asyncio.run(pt.run_pass(w.market.conn, w.research.conn, at(s, 15, 55), board=None, schwab=quotes,
                                    chains=None, rate=rate, clock=partial(at, s, 15, 55)))
        out = pl.build(w.market, w.research, at(s, 16, 20))
        assert out["asof"] == s.isoformat() and out["recorded"]
        if s == sessions[0]:
            short_dated(w, s)
        if s != off:
            asyncio.run(pt.run_pass(w.market.conn, w.research.conn, at(s, 16, 30), board=None, schwab=quotes,
                                    chains=None, rate=rate))
        snapshots[s] = {tuple(r)[:5]: tuple(r) for r in w.research.conn.execute("SELECT * FROM stk_play_marks")}
    # the morning after: the laptop wakes up and repairs whatever is left
    report = asyncio.run(pt.run_pass(w.market.conn, w.research.conn, at(cal.next_trading_day(sessions[-1]), 8),
                                     board=None, schwab=None, chains=None, rate=rate))
    w.sessions, w.off, w.snapshots, w.report = sessions, off, snapshots, report  # type: ignore[attr-defined]
    yield w
    w.close()


def test_every_pick_and_its_twin_is_marked_every_session_until_it_settles(history: Any) -> None:
    conn = history.research.conn
    plays = [(r[0], r[1], r[2], 0, r[3]) for r in conn.execute("SELECT session, category, rank, expiration FROM "
                                                               "stk_plays")]
    twins = [(r[0], r[1], r[2], 1, r[3]) for r in conn.execute("SELECT session, category, rank, expiration FROM "
                                                               "stk_play_twins")]
    assert len(plays) > 20 and len(twins) >= 0.8 * len(plays)
    last = history.sessions[-1]
    marks: dict[tuple[int, str, int, int], set[int]] = {}
    for r in conn.execute("SELECT session, category, rank, twin, day FROM stk_play_marks"):
        marks.setdefault((r[0], r[1], r[2], r[3]), set()).add(r[4])
    for session, category, rank, twin, expiration in plays + twins:
        through = min(pm.settle_day(date.fromordinal(expiration)), last)
        expected = {day_of(d) for d in cal.trading_days(date.fromordinal(session), through)}
        assert marks.get((session, category, rank, twin)) == expected, (session, category, rank, twin)
    assert history.report["marks"]["pending"] == 0 and history.report["marks"]["provisional"] == 0


def test_tracked_sessions_are_quoted_the_laptop_off_session_is_estimated_and_expiries_settle(history: Any) -> None:
    conn = history.research.conn
    status: dict[date, dict[str, int]] = {}
    for r in conn.execute("SELECT day, status, COUNT(*) n FROM stk_play_marks GROUP BY day, status"):
        status.setdefault(date.fromordinal(r[0]), {})[r[1]] = r[2]
    off = history.off
    assert status[off].get("estimated")
    # on the laptop-off session only real recorded quotes are "quoted": the session's own picks, and older picks whose
    # every contract was picked again that day
    for r in conn.execute("SELECT session, category, rank, twin FROM stk_play_marks WHERE day = ? AND status = "
                          "'quoted'", (day_of(off),)):
        table = "stk_play_twins" if r["twin"] else "stk_plays"
        legs = json.loads(conn.execute(f"SELECT legs_json FROM {table} WHERE session = ? AND category = ? AND "
                                       "rank = ?", (r[0], r[1], r[2])).fetchone()[0])
        sources = {conn.execute("SELECT source FROM stk_track_days WHERE occ = ? AND session = ?",
                                (leg["occ"], day_of(off))).fetchone()[0] for leg in legs}
        assert sources == {"recorded play"}
    for d in history.sessions:
        if d != off:
            assert set(status[d]) <= {"quoted", "settled", "modelled"} and status[d].get("quoted")
    notes = [r[0] for r in conn.execute("SELECT note FROM stk_track_days WHERE session = ? AND status = 'estimated'",
                                        (day_of(off),))]
    assert notes and all("laptop was off" in n for n in notes)
    settled = conn.execute("SELECT COUNT(*) FROM stk_play_marks WHERE status = 'settled'").fetchone()[0]
    expired = conn.execute("SELECT COUNT(*) FROM stk_plays WHERE expiration <= ?",
                           (day_of(history.sessions[-1]),)).fetchone()[0]
    assert settled >= expired > 0
    # the settlement agrees with the existing grade at expiration
    for r in conn.execute("SELECT m.pnl_pct, o.net_return FROM stk_play_marks m JOIN stk_play_outcomes o USING "
                          "(session, category, rank) WHERE m.status = 'settled' AND m.twin = 0"):
        assert r[0] == pytest.approx(r[1], abs=1e-4)


def test_later_sessions_never_change_an_earlier_mark(history: Any) -> None:
    final = {tuple(r)[:5]: tuple(r) for r in history.research.conn.execute("SELECT * FROM stk_play_marks")}
    for snapshot in history.snapshots.values():
        assert all(final[k] == v for k, v in snapshot.items())


def test_the_first_day_is_the_recorded_close_and_costs_the_spread(history: Any) -> None:
    conn = history.research.conn
    rows = conn.execute("SELECT m.pnl, m.status, p.action FROM stk_play_marks m JOIN stk_plays p USING (session, "
                        "category, rank) WHERE m.day = m.session AND m.twin = 0").fetchall()
    assert rows and all(r["status"] == "quoted" for r in rows)
    assert all(r["pnl"] < 0 for r in rows if r["action"] == "BUY")    # bought at the ask, valued at the bid, fees


def test_tracking_status_reports_storage(history: Any, tmp_path: Path) -> None:
    status = pt.status(history.research.conn, at(history.sessions[-1], 17))
    assert status["storage"]["rows"]["stk_play_marks"] > 0 and status["last_close_folded"]
    assert status["storage"]["rows"]["stk_track_ticks"] == 0          # every session was folded


