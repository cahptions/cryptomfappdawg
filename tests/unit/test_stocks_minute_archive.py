"""Minute bars the app already fetches are kept after Schwab's ten days: finished sessions only, only ever added to,
capped by size with the oldest sessions pruned first, and gap repair reads them for older sessions without asking."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from pathlib import Path

from desk.stocks import calendar as cal
from desk.stocks import minute_archive as ma
from desk.stocks.contracts import IntradayBar
from desk.stocks.play_marks import estimate_intraday
from tests.stocks_world import make_world

DAY = date(2026, 10, 1)


def minutes(symbol: str, d: date, n: int = 390, price: float = 50.0, start: int = 0) -> list[IntradayBar]:
    opened = datetime.combine(d, time(9, 30), cal.NEW_YORK)
    return [IntradayBar(symbol, opened + timedelta(minutes=start + k), price, price + .1, price - .1, price + .05, 1e3)
            for k in range(n)]


def test_finished_sessions_are_kept_merged_and_pruned_oldest_first(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=8)
    try:
        conn = w.market.conn
        during = datetime.combine(DAY, time(12), cal.NEW_YORK)
        assert ma.archive(conn, minutes("S00", DAY, 100), during) == 0               # session still running
        after = cal.session_close(DAY) + timedelta(hours=1)
        assert ma.archive(conn, minutes("S00", DAY, 100), after) == 100
        assert ma.archive(conn, minutes("S00", DAY, 100, price=99.0), after) == 0     # a stored minute is never rewritten
        assert ma.archive(conn, minutes("S00", DAY, 50, start=100), after) == 50
        kept = ma.read(conn, "S00", DAY)
        assert len(kept) == 150 and kept[0].close == 50.05 and kept[0].at == minutes("S00", DAY, 1)[0].at
        size = conn.execute("SELECT bytes FROM stk_minute_bars").fetchone()[0]
        assert size < 6_000
        later = cal.next_trading_day(DAY)
        ma.archive(conn, minutes("S00", later, 150), cal.session_close(later) + timedelta(hours=1), max_bytes=size * 3 // 2)
        assert ma.sessions(conn, "S00") == [later]                                   # over the cap: the oldest goes
    finally:
        w.close()


def test_gap_repair_reads_archived_minutes_for_sessions_schwab_no_longer_serves(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=8)
    try:
        last_closed = date(2026, 10, 30)
        old = date(2026, 10, 1)                                                        # well past ten trading days
        ma.archive(w.market.conn, minutes("S00", old), cal.session_close(old) + timedelta(hours=1))
        asked: list[str] = []

        class History:
            configured = True

            async def intraday_history(self, symbol: str, *, days: int) -> list[IntradayBar]:
                asked.append(symbol)
                return []

        occ = "S00261120C00050000"
        spent, errors = asyncio.run(estimate_intraday(w.dbs.research, History(), [(occ, old, 0.4)],  # type: ignore[arg-type]
                                                      last_closed, lambda d: 0.04, datetime.now(cal.NEW_YORK), 5,
                                                      market=w.market.conn))
        assert spent == 0 and not asked and not errors
        row = w.dbs.research.execute("SELECT status, points FROM stk_track_intraday WHERE occ = ?", (occ,)).fetchone()
        assert row["status"] == "estimated" and row["points"] > 70
        none, _ = asyncio.run(estimate_intraday(w.dbs.research, History(), [(occ, date(2026, 9, 1), 0.4)],  # type: ignore[arg-type]
                                                last_closed, lambda d: 0.04, datetime.now(cal.NEW_YORK), 5,
                                                market=w.market.conn))
        assert none == 0 and not asked                                                # nothing archived: left alone
    finally:
        w.close()
