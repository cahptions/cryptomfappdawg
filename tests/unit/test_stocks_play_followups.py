"""The Unusual follow-up: when a flagged contract is missing from today's chains, its own tracked quotes (a live tick
of today, else a stored closing quote of a later session) answer whether the flagged volume opened positions."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from desk.core.paths import DeskPaths
from desk.stocks import play_tracking as pt
from desk.stocks import plays as pl
from desk.stocks.contracts import OptionTick
from desk.stocks.repo import StocksDatabases, StocksResearch, day_of
from tests.conftest import REPO

FLAGGED_ON = date(2026, 11, 3)
OCCS = ["AAA261218C00050000", "BBB261218C00050000", "CCC261218C00050000"]


@pytest.fixture
def dbs(tmp_path: Path) -> Any:
    out = StocksDatabases.open(DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env"))
    yield out
    out.close()


def _day(conn: Any, occ: str, status: str, bid: float, ask: float, oi: int | None, quoted_at: str | None) -> None:
    conn.execute("INSERT INTO stk_track_days(occ, session, status, bid, ask, open_interest, quoted_at, source, "
                 "recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'schwab', 'x')",
                 (occ, day_of(date(2026, 11, 4)), status, bid, ask, oi, quoted_at))


def _tick(conn: Any, occ: str, bid: float, oi: int, at: datetime, session: date) -> None:
    tick = OptionTick(occ, bid, bid + 0.2, None, None, 10, oi, None, None, None, None, None, 50.0, at)
    pt.save_ticks(conn, pt.tick_rows([tick], at, session, "schwab"))


def test_a_follow_up_falls_back_to_the_contracts_tracked_quotes(dbs: Any) -> None:
    conn = dbs.research
    flag = {"session": FLAGGED_ON.isoformat(), "kind": "call", "strike": 50.0, "expiration": "2026-12-18",
            "volume": 4000, "open_interest": 1000, "entry": 2.0, "tier": "Whale"}
    flagged = {occ: {**flag, "symbol": occ[:3]} for occ in OCCS}
    research = StocksResearch(conn)
    nothing = {r["occ"]: r for r in pl.followups(flagged, [], research)}
    assert all(r["opened"] is None and r["source"] is None for r in nothing.values())
    assert "tracking has no later quote yet" in nothing[OCCS[0]]["verdict"]
    _day(conn, OCCS[0], "quoted", 2.4, 2.6, 4200, "2026-11-04T20:59:00+00:00")   # the next session's close
    _day(conn, OCCS[1], "quoted", 1.0, 1.2, 1100, None)
    _day(conn, OCCS[2], "estimated", 9.0, 9.0, None, None)                     # an estimate is never a quote
    at = datetime(2026, 11, 5, 15, 30, tzinfo=UTC)
    _tick(conn, OCCS[1], 1.4, 1150, at, date(2026, 11, 5))                     # a live tick of today wins
    _tick(conn, OCCS[2], 3.0, 9000, at, FLAGGED_ON)                            # the flag day's own: no later OI
    got = {r["occ"]: r for r in pl.followups(flagged, [], research)}
    assert got[OCCS[0]]["open_interest_now"] == 4200 and got[OCCS[0]]["opened"] == 0.8
    assert got[OCCS[0]]["source"] == "tracking's closing quote" and got[OCCS[0]]["price_change"] == 0.25
    assert got[OCCS[0]]["verdict"].startswith("Opened")
    assert got[OCCS[1]]["source"] == "tracking's latest quote" and got[OCCS[1]]["open_interest_now"] == 1150
    assert datetime.fromisoformat(got[OCCS[1]]["source_at"]) == at and got[OCCS[1]]["verdict"].startswith("Mostly closing")
    assert got[OCCS[2]]["source"] is None and got[OCCS[2]]["opened"] is None
