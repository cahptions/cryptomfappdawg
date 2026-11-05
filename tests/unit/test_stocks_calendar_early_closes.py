"""NYSE half days: 1:00 PM closes on the day after Thanksgiving, Christmas Eve and July 3 when each is a trading day,
and every closing window (tracking's last quote, recording, PAPER entries) follows the real close."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from desk.stocks import calendar as cal
from desk.stocks import play_tracking as pt
from desk.stocks import plays as pl


@pytest.mark.parametrize("year,expected", [
    (2019, {date(2019, 7, 3), date(2019, 11, 29), date(2019, 12, 24)}),
    (2021, {date(2021, 11, 26)}),                     # July 4 a Sunday; December 24 the observed Christmas
    (2022, {date(2022, 11, 25)}),                     # July 4 a Monday; December 24 a Saturday
    (2023, {date(2023, 7, 3), date(2023, 11, 24)}),   # December 24 a Sunday
    (2024, {date(2024, 7, 3), date(2024, 11, 29), date(2024, 12, 24)}),
    (2025, {date(2025, 7, 3), date(2025, 11, 28), date(2025, 12, 24)}),
    (2026, {date(2026, 11, 27), date(2026, 12, 24)}),  # July 3 is the observed Independence Day
    (2027, {date(2027, 11, 26)}),
])
def test_the_exchanges_early_closes_by_rule(year: int, expected: set[date]) -> None:
    assert cal.early_closes(year) == expected
    for d in expected:
        assert cal.is_trading_day(d) and cal.close_time(d) == time(13, 0)
        assert cal.session_close(d) == datetime(d.year, d.month, d.day, 13, tzinfo=cal.NEW_YORK)
    assert cal.session_close(date(year, 11, 2 + (7 - date(year, 11, 2).weekday()) % 7)).hour == 16   # a Monday


def test_a_half_day_ends_the_session_and_its_windows_at_one() -> None:
    day = date(2026, 11, 27)
    at = lambda h, m: datetime(2026, 11, 27, h, m, tzinfo=cal.NEW_YORK)  # noqa: E731
    assert cal.market_session(at(12, 59))["state"] == "open"
    assert cal.market_session(at(13, 1))["state"] == "after"
    assert cal.last_closed_session(at(13, 30)) == day
    assert cal.last_closed_session(at(12, 30)) == cal.previous_trading_day(day)
    assert not pl.session_closed(day, at(13, 10)) and pl.session_closed(day, at(13, 16))
    assert not pt.market_open(at(13, 5)) and pt.market_open(at(12, 55))
    # the closing window: ten minutes before the real close every contract is due once more
    contract = pt.Tracked("AAA261218C00100000", "AAA", "call", 100.0, date(2026, 12, 18), 1)
    seen = {contract.occ: at(12, 45)}
    assert pt.due({contract.occ: contract}, seen, at(12, 48)) == []
    assert pt.due({contract.occ: contract}, seen, at(12, 51)) == [contract]
    normal = datetime(2026, 11, 25, 12, 51, tzinfo=cal.NEW_YORK)
    assert pt.due({contract.occ: contract}, {contract.occ: normal - timedelta(minutes=6)}, normal) == []


def test_a_half_days_closing_quote_is_its_one_oclock_quote(tmp_path: Path) -> None:
    from desk.core.paths import DeskPaths
    from desk.stocks.contracts import OptionTick
    from desk.stocks.repo import StocksDatabases
    from tests.conftest import REPO

    dbs = StocksDatabases.open(DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env"))
    day = date(2026, 11, 27)
    at = lambda h, m: datetime(2026, 11, 27, h, m, tzinfo=cal.NEW_YORK)  # noqa: E731
    occs = ("AAA261218C00100000", "BBB261218C00100000")
    # AAA quoted in the last hour of the half day; BBB only in the morning: no closing quote for it
    ticks = [OptionTick(occs[0], 3.0, 3.2, None, None, 10, 100, 0.3, 0.5, 0.02, -0.02, 0.1, 100.0, at(12, 55)),
             OptionTick(occs[1], 2.0, 2.2, None, None, 10, 100, 0.3, 0.5, 0.02, -0.02, 0.1, 100.0, at(10, 0))]
    with dbs.research:
        for t in ticks:
            pt.save_ticks(dbs.research, pt.tick_rows([t], t.quoted_at or at(9, 30), day, "test"))
    assert pt.fold(dbs.research, day, at(13, 10), lambda _: 0.04) == 2
    rows = {r["occ"]: r for r in dbs.research.execute("SELECT * FROM stk_track_days WHERE session = ?",
                                                       (day.toordinal(),))}
    assert set(rows) == {occs[0]} and rows[occs[0]]["bid"] == 3.0
    packed = dbs.research.execute("SELECT points FROM stk_track_intraday WHERE occ = ?", (occs[0],)).fetchone()
    assert packed is not None and packed[0] == 1
    dbs.close()


def test_a_filing_after_a_half_days_close_counts_from_the_next_session() -> None:
    from desk.stocks import signals

    half, full = date(2026, 11, 27), date(2026, 11, 25)
    ny = lambda d, h, m: datetime.combine(d, time(h, m), cal.NEW_YORK)  # noqa: E731
    assert signals._session_after(ny(half, 12, 59), half) == half
    assert signals._session_after(ny(half, 14, 0), half) == cal.next_trading_day(half)      # after the 1:00 PM close
    assert signals._session_after(ny(full, 14, 0), full) == full                            # a full day: still open
    assert signals._session_after(ny(full, 16, 0), full) == cal.next_trading_day(full)
