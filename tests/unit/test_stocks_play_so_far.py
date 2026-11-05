"""Before any of a section's plays has expired, its track record says how its picks stand so far: the longest holding
period with quoted closing marks, counted as Performance counts it (estimates never)."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from desk.core.paths import DeskPaths
from desk.stocks import calendar as cal
from desk.stocks import play_performance as perf
from desk.stocks.repo import StocksDatabases, day_of
from tests.conftest import REPO

START = date(2026, 9, 1)


@pytest.fixture
def research(tmp_path: Path) -> Any:
    dbs = StocksDatabases.open(DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env"))
    yield dbs.research
    dbs.close()


def _play(conn: Any, session: date, category: str, path: list[tuple[float, str]]) -> None:
    conn.execute(
        "INSERT INTO stk_plays(session, category, rank, symbol, action, structure, expiration, event, event_kind,"
        " event_strike, event_width, entry, max_loss, legs_json, underlying, itm_market, itm_history, itm_kestrel,"
        " itm_kestrel_raw, profit_kestrel, play_json, method, recorded_at) VALUES (?, ?, 1, 'X', 'BUY', 'long_call',"
        " ?, 'itm', 'call', 10, 0, 1, 101, '[]', 10, 0.5, 0.5, 0.5, 0.5, 0.4, '{}', 'm', 'x')",
        (day_of(session), category, day_of(session + timedelta(days=60))))
    days = cal.trading_days(session, session + timedelta(days=30))
    conn.executemany("INSERT INTO stk_play_marks(session, category, rank, twin, day, status, value, mid, pnl, pnl_pct,"
                     " underlying, note, recorded_at) VALUES (?, ?, 1, 0, ?, ?, 1, 1, 0, ?, 10, NULL, 'x')",
                     [(day_of(session), category, day_of(days[i]), status, r) for i, (r, status) in enumerate(path)])


def test_a_section_without_expiries_shows_its_picks_so_far(research: Any) -> None:
    sessions = cal.trading_days(START, START + timedelta(days=20))
    week = [(0.0, "quoted"), (0.1, "quoted"), (0.1, "quoted"), (0.1, "quoted"), (0.1, "quoted")]
    for i, ret in enumerate((0.2, 0.4, -0.1)):
        _play(research, sessions[i], "edge", [*week, (ret, "quoted")])               # a week of quoted marks
    _play(research, sessions[3], "edge", [*week, (0.9, "estimated")])                # an estimate: not counted
    _play(research, sessions[0], "lottos", [(0.0, "quoted"), (-0.3, "quoted")])      # one day only
    out = perf.so_far(research, ["edge", "lottos", "squeeze"])
    assert out["edge"] == {"label": "1 week", "sessions": 5, "n": 3, "mean": pytest.approx(0.16667, abs=1e-4),
                           "median": 0.2, "hit_rate": pytest.approx(0.6667, abs=1e-4)}
    assert out["lottos"]["label"] == "1 day" and out["lottos"]["median"] == -0.3
    assert "squeeze" not in out and perf.so_far(research, []) == {}
