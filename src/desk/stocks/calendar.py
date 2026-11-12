"""The NYSE trading calendar by rule: weekends, the exchange's full-day holidays and its early closes (1:00 PM on
the day after Thanksgiving, on Christmas Eve and on July 3 when each is a trading day; equity options close with
the stocks). ``session_close`` is the one place a session's close is decided, so tracking's closing quotes,
freshness and recording follow a half day. Times are America/New_York."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import cache
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)


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


@cache
def federal_holidays(year: int) -> frozenset[date]:
    """US federal holidays as observed (a Saturday holiday on the Friday before, a Sunday one on the Monday after, so
    New Year's Day on a Saturday is observed on December 31). The SEC, and so EDGAR's daily index, is closed on them,
    including Columbus Day and Veterans Day, when the stock market is open."""
    def observed(d: date) -> date:
        return d - timedelta(days=1) if d.weekday() == 5 else d + timedelta(days=1) if d.weekday() == 6 else d

    fixed = [date(year, 1, 1), date(year, 7, 4), date(year, 11, 11), date(year, 12, 25)]
    if year >= 2021:
        fixed.append(date(year, 6, 19))
    days = {observed(d) for d in fixed} | {d for d in (observed(date(year + 1, 1, 1)),) if d.year == year}
    days |= {_nth_weekday(year, 1, 0, 3), _nth_weekday(year, 2, 0, 3), _last_weekday(year, 5, 0),
             _nth_weekday(year, 9, 0, 1), _nth_weekday(year, 10, 0, 2), _nth_weekday(year, 11, 3, 4)}
    return frozenset(d for d in days if d.year == year)


def is_edgar_day(d: date) -> bool:
    """A day EDGAR publishes a daily index: a weekday that isn't a federal holiday. Not the trading calendar: EDGAR is
    open on Good Friday and closed on Columbus Day and Veterans Day."""
    return d.weekday() < 5 and d not in federal_holidays(d.year)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in holidays(d.year)


@cache
def early_closes(year: int) -> frozenset[date]:
    """NYSE's 1:00 PM closes (Rule 7.2 schedule): the Friday after Thanksgiving; December 24 when it is a trading day
    (a Monday to Thursday: on a Friday it is the observed Christmas holiday); July 3 when it is a trading day, which
    is when Independence Day falls on a Tuesday to Friday (on a Saturday, July 3 is the observed holiday itself; on a
    Sunday or Monday the day before is a weekend and the session before it closes at 4:00)."""
    days = (_nth_weekday(year, 11, 3, 4) + timedelta(days=1), date(year, 12, 24), date(year, 7, 3))
    return frozenset(d for d in days if is_trading_day(d))


def close_time(d: date) -> time:
    """The regular session's closing time on ``d``: 1:00 PM on an early close, otherwise 4:00 PM."""
    return EARLY_CLOSE if d in early_closes(d.year) else CLOSE


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
    return datetime.combine(d, close_time(d), NEW_YORK)


def market_session(now: datetime) -> dict[str, str]:
    """Where the US market is right now: ``pre`` (4:00–9:30 ET on a trading day), ``open`` (the regular session, to
    1:00 PM on an early close), ``after`` (to 8:00 PM) or ``closed`` (nights, weekends, holidays), with the trading
    day it belongs to."""
    local = new_york(now)
    d, t = local.date(), local.time()
    if not is_trading_day(d):
        return {"state": "closed", "session": previous_trading_day(d).isoformat()}
    if t < time(4, 0):
        return {"state": "closed", "session": previous_trading_day(d).isoformat()}
    if t < time(9, 30):
        return {"state": "pre", "session": d.isoformat()}
    if t < close_time(d):
        return {"state": "open", "session": d.isoformat()}
    return {"state": "after" if t < time(20, 0) else "closed", "session": d.isoformat()}


def last_closed_session(now: datetime, after_close: timedelta = timedelta(0)) -> date:
    """The latest trading day whose close (plus ``after_close``) has passed."""
    local = new_york(now)
    d = local.date()
    if not is_trading_day(d) or local < session_close(d) + after_close:
        d = previous_trading_day(d)
    return d


__all__ = ["CLOSE", "EARLY_CLOSE", "NEW_YORK", "close_time", "early_closes", "federal_holidays", "holidays",
           "is_edgar_day", "is_trading_day",
           "last_closed_session", "market_session", "new_york", "next_trading_day", "previous_trading_day",
           "session_close", "trading_days"]
