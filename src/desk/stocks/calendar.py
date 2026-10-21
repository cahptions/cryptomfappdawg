"""The NYSE trading calendar by rule: weekends and the exchange's full-day holidays (early closes are ignored; the
desk works on end-of-day data). Times are America/New_York."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import cache
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
CLOSE = time(16, 0)


def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(d: date) -> date | None:
    if d.weekday() == 5:
        # a Saturday holiday moves to Friday, except New Year's Day (the exchange stays open on December 31)
        return None if (d.month, d.day) == (1, 1) else d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


@cache
def holidays(year: int) -> frozenset[date]:
    fixed = [date(year, 1, 1), date(year, 7, 4), date(year, 12, 25)]
    if year >= 2022:
        fixed.append(date(year, 6, 19))
    days = {o for d in fixed if (o := _observed(d)) is not None}
    days |= {_nth_weekday(year, 1, 0, 3), _nth_weekday(year, 2, 0, 3), _easter(year) - timedelta(days=2),
             _last_weekday(year, 5, 0), _nth_weekday(year, 9, 0, 1), _nth_weekday(year, 11, 3, 4)}
    return frozenset(days)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in holidays(d.year)


def trading_days(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def previous_trading_day(d: date) -> date:
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def next_trading_day(d: date) -> date:
    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def new_york(now: datetime) -> datetime:
    return now.astimezone(NEW_YORK)


def session_close(d: date) -> datetime:
    return datetime.combine(d, CLOSE, NEW_YORK)


def last_closed_session(now: datetime, after_close: timedelta = timedelta(0)) -> date:
    """The latest trading day whose close (plus ``after_close``) has passed."""
    local = new_york(now)
    d = local.date()
    if not is_trading_day(d) or local < session_close(d) + after_close:
        d = previous_trading_day(d)
    return d


__all__ = ["NEW_YORK", "holidays", "is_trading_day", "last_closed_session", "new_york", "next_trading_day",
           "previous_trading_day", "session_close", "trading_days"]
