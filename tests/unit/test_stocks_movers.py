"""Home's movers card: today's biggest moves among the stocks the app already quotes on Schwab this session (Plays
tracking's option quotes carry the underlying's price; collected chains carry it too), against the last close."""

from datetime import UTC, date, datetime, time
from pathlib import Path

from desk.stocks import calendar as cal
from desk.stocks.hub import intraday_movers
from desk.stocks.repo import day_of
from tests.stocks_world import make_world

SESSION = date(2026, 10, 22)
NOW = datetime.combine(SESSION, time(11, 0), cal.NEW_YORK).astimezone(UTC)


def _tick(w, occ: str, underlying: float, minute: int) -> None:
    at = datetime.combine(SESSION, time(10, minute), cal.NEW_YORK).astimezone(UTC).isoformat()
    w.research.conn.execute("INSERT INTO stk_track_ticks(occ, at, session, underlying, quoted_at, source) "
                            "VALUES (?,?,?,?,?,?)", (occ, at, day_of(SESSION), underlying, at, "schwab"))


def test_movers_rank_the_newest_quoted_price_against_the_last_close(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=12)
    try:
        last = {s: next(b.close for b in reversed(w.market.bars(s)) if b.day < SESSION) for s in ("CHEAP", "SQZ", "S10")}
        _tick(w, "CHEAP261120C00050000", last["CHEAP"] * 1.01, 5)
        _tick(w, "CHEAP261120P00050000", last["CHEAP"] * 1.06, 40)          # the newer tick wins
        _tick(w, "SQZ261120C00050000", last["SQZ"] * 0.93, 30)
        _tick(w, "S10261120C00050000", last["S10"] * 1.02, 30)
        got = intraday_movers(w.market.conn, w.research.conn, NOW)
        assert got["quoted"] == 3 and [r["symbol"] for r in got["up"]] == ["CHEAP", "S10"]
        assert abs(got["up"][0]["change"] - 0.06) < 1e-9 and got["up"][0]["source"] == "Plays tracking quotes"
        assert [r["symbol"] for r in got["down"]] == ["SQZ"]
        before = datetime.combine(SESSION, time(9, 0), cal.NEW_YORK).astimezone(UTC)
        assert intraday_movers(w.market.conn, w.research.conn, before)["quoted"] == 0      # not before the open
    finally:
        w.close()
