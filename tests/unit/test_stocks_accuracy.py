"""Accuracy of output (desk.stocks.accuracy): each invariant catches a deliberately broken record and passes a valid
one; the fixes it led to are tested where they were made."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from desk.storage.db import DbKind, migrate

MIGRATIONS = Path(__file__).resolve().parents[2] / "src" / "desk" / "storage" / "migrations" / "stocks_research"


def test_the_mark_floor_migration_corrects_only_marks_below_minus_100_percent() -> None:
    """Marks recorded before play_marks.profit stopped a loss at the money at risk are corrected to exactly -100%;
    every other mark, settlements and the append-only rule are unchanged."""
    conn = sqlite3.connect(":memory:")
    migrate(conn, DbKind.STOCKS_RESEARCH)
    rows = [("lotto", -14.0, -1.0769, "quoted"),          # money at risk 13.0: corrected to -13.0
            ("fly", -91.6, -1.198, "quoted"),              # an iron butterfly bought back above its width
            ("fine", -6.5, -0.5, "quoted"), ("won", 20.0, 0.4, "settled"), ("zero", -13.0, -1.0, "estimated")]
    for i, (category, pnl, pct, status) in enumerate(rows):
        conn.execute("INSERT INTO stk_play_marks(session, category, rank, twin, day, status, value, pnl, pnl_pct, "
                     "recorded_at) VALUES (1, ?, 1, 0, ?, ?, 0, ?, ?, 'x')", (category, i + 1, status, pnl, pct))
    conn.execute("INSERT INTO stk_play_marks_provisional(session, category, rank, twin, day, value, pnl, pnl_pct, "
                 "computed_at) VALUES (1, 'lotto', 1, 0, 9, 0, -14.0, -1.0769, 'x')")
    sql = (MIGRATIONS / "0018_mark_floor.sql").read_text()
    conn.executescript(sql)                               # idempotent: applied again changes nothing more
    got = {r[0]: (round(r[1], 4), r[2]) for r in conn.execute("SELECT category, pnl, pnl_pct FROM stk_play_marks")}
    assert got["lotto"] == (round(-14.0 / 1.0769, 4), -1.0)
    assert got["fly"] == (round(-91.6 / 1.198, 4), -1.0)
    assert got["fine"] == (-6.5, -0.5) and got["won"] == (20.0, 0.4) and got["zero"] == (-13.0, -1.0)
    assert conn.execute("SELECT pnl_pct FROM stk_play_marks_provisional").fetchone()[0] == -1.0
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE stk_play_marks SET pnl = 0")
