"""The same-evening close from Schwab: five minutes after the 16:00 New York close, every stored stock's regular-session
open, high, low, close and volume, in batches of 100, so the day's research runs that evening instead of waiting for
the free bulk feed's next-morning publication.

Massive stays the history source and the fallback (Schwab signed out, a thin answer). When Massive publishes the same
day, its closes are compared with Schwab's. Stored bars are never rewritten, and if Schwab's closes disagree too often
the same-evening close switches itself off, so research goes back to waiting for Massive.
"""

from __future__ import annotations

import statistics
from datetime import date, datetime, timedelta
from typing import Any

from desk.core.clock import to_iso
from desk.core.errors import DeskError, ProviderError
from desk.stocks import calendar as cal
from desk.stocks.contracts import Bar
from desk.stocks.repo import StocksMarket, date_of, day_of
from desk.stocks.universe import stored_symbols

JOB = "schwab_close"
SOURCE = "schwab_close"
WAIT = timedelta(minutes=5)            # after the 16:00 close, for closing prints to settle
WINDOW = timedelta(hours=4)            # until extended hours end; later, the next morning's Massive file is used
RETRY = timedelta(minutes=15)
BATCH = 100
MIN_COVERAGE = 0.8                     # of the stocks with a bar on the previous stored session
CLOSE_TOLERANCE = 0.005                # a Schwab close within 0.5% of Massive's agrees
MAX_DISAGREEMENT = 0.05                # more than 5% of stocks disagreeing switches the same-evening close off
VERIFY_DAYS = 10


def closing_bar(symbol: str, row: dict[str, Any], day: date) -> Bar | None:
    """A daily bar from Schwab's ``quote`` and ``regular`` sections, or None when any part is missing or implausible.

    Close is the regular session's last trade (``regularMarketLastPrice``), and it must have traded on ``day``."""
    values = [v for key in ("regular_last", "open", "high", "low") if isinstance(v := row.get(key), (int, float))]
    stamp, volume = row.get("regular_trade_time"), row.get("volume")
    if len(values) != 4 or not isinstance(stamp, str):
        return None
    close, opened, high, low = (float(v) for v in values)
    if cal.new_york(datetime.fromisoformat(stamp)).date() != day:
        return None
    if min(close, opened, high, low) <= 0 or high < low:
        return None
    if high < max(opened, close) * (1 - 1e-9) or low > min(opened, close) * (1 + 1e-9):
        return None
    return Bar(symbol, day, opened, high, low, close, float(volume) if isinstance(volume, (int, float)) else 0.0)


def _detail(market: StocksMarket) -> dict[str, Any]:
    value = market.jobs().get(JOB, {}).get("detail", {})
    return value if isinstance(value, dict) else {}


def due(market: StocksMarket, now: datetime) -> date | None:
    """Today's session, when its close is between 5 minutes and 4 hours old and no bars are stored for it yet."""
    day = cal.new_york(now).date()
    if not cal.is_trading_day(day):
        return None
    close = cal.session_close(day)
    if not close + WAIT <= now <= close + WINDOW or day in market.checked_days():
        return None
    detail = _detail(market)
    if detail.get("disabled"):
        return None
    tried = market.jobs().get(JOB, {}).get("last_run_at")
    if detail.get("day") == day.isoformat() and tried and now - datetime.fromisoformat(tried) < RETRY:
        return None
    return day


def _expected(market: StocksMarket, day: date) -> int:
    row = market.conn.execute("SELECT rows FROM stk_bar_days WHERE day < ? AND rows > 0 ORDER BY day DESC LIMIT 1",
                              (day_of(day),)).fetchone()
    return int(row[0]) if row else 0


async def collect(schwab: Any, market: StocksMarket, now: datetime) -> dict[str, Any]:
    """Fetch and store today's closing bars when due. Too thin an answer stores nothing (Massive fills the day)."""
    day = due(market, now)
    if day is None:
        return {"skipped": "not due"}
    detail = {**_detail(market), "day": day.isoformat()}
    symbols = sorted(stored_symbols(market))
    bars: list[Bar] = []
    for start in range(0, len(symbols), BATCH):
        rows = await schwab.closing_quotes(symbols[start:start + BATCH])
        bars += [bar for symbol, row in rows.items() if (bar := closing_bar(symbol, row, day)) is not None]
    expected = _expected(market, day)
    coverage = len(bars) / expected if expected else 0.0
    if coverage < MIN_COVERAGE:
        market.job(JOB, now, detail=detail)
        return {**detail, "stored": 0, "coverage": round(coverage, 3), "waiting": (
            f"Schwab returned usable closes for {len(bars)} of about {expected} stocks; retrying, then Massive.")}
    rows = market.save_bars(day, bars, SOURCE, now)
    detail.update(stored=rows, coverage=round(coverage, 3), at=to_iso(now))
    market.job(JOB, now, detail=detail)
    return {**detail, "requests": -(-len(symbols) // BATCH)}


async def verify(massive: Any, market: StocksMarket, now: datetime) -> dict[str, Any]:
    """Compare recent Schwab-sourced days with Massive's bars once Massive publishes them. Never rewrites a bar."""
    detail = _detail(market)
    checked = detail.get("verified", {})
    since = day_of(now.date() - timedelta(days=VERIFY_DAYS))
    days = [date_of(r[0]) for r in market.conn.execute(
        "SELECT day FROM stk_bar_days WHERE source=? AND day>=? ORDER BY day", (SOURCE, since))]
    report: dict[str, Any] = {}
    for day in days:
        if day.isoformat() in checked:
            continue
        try:
            official = await massive.grouped_daily(day)
        except ProviderError as exc:
            if exc.status_code == 403:        # not published yet on the free plan
                continue
            raise
        if not official:
            continue
        ours = {r["symbol"]: r["close"] for r in market.conn.execute(
            "SELECT symbol, close FROM stk_bars WHERE day=?", (day_of(day),))}
        diffs = sorted(((abs(ours[b.symbol] / b.close - 1), b.symbol) for b in official
                        if b.symbol in ours and b.close > 0), reverse=True)
        if not diffs:
            continue
        disagree = [s for d, s in diffs if d > CLOSE_TOLERANCE]
        result = {"compared": len(diffs), "disagree": len(disagree), "worst": [s for _, s in diffs[:3]],
                  "median_difference": round(statistics.median(d for d, _ in diffs), 6)}
        checked[day.isoformat()] = result
        report[day.isoformat()] = result
        if len(disagree) > MAX_DISAGREEMENT * len(diffs):
            detail["disabled"] = (f"Schwab's closes for {day.isoformat()} disagreed with Massive for {len(disagree)} "
                                  f"of {len(diffs)} stocks; research waits for Massive again.")
    if report:
        detail["verified"] = dict(sorted(checked.items())[-VERIFY_DAYS:])
        market.job(JOB, now, detail=detail)
    return {"verified": report, **({"disabled": detail["disabled"]} if detail.get("disabled") else {})}


def target_session(market: StocksMarket, now: datetime, after_close: timedelta) -> date:
    """The session research should describe: today, as soon as its closing bars are stored (from Schwab), otherwise
    the usual close-plus-delay rule that waits for the bulk feed."""
    quick = cal.last_closed_session(now, WAIT)
    if market.conn.execute("SELECT 1 FROM stk_bar_days WHERE day=? AND rows>0", (day_of(quick),)).fetchone():
        return quick
    return cal.last_closed_session(now, after_close)


async def job(suite: Any, market: StocksMarket, now: datetime) -> dict[str, Any]:
    """The worker's pass: the same-evening close when due, then any pending comparison with Massive."""
    from desk.stocks.collect import _job
    from desk.stocks.schwab_auth import SchwabAuthError

    async def work() -> dict[str, Any]:
        report: dict[str, Any] = {}
        if suite.schwab is not None and getattr(suite.schwab, "configured", False):
            try:
                report["close"] = await collect(suite.schwab, market, now)
            except SchwabAuthError as exc:
                report["close"] = {"skipped": f"Schwab sign-in needed: {str(exc)[:120]}"}
        if getattr(suite.massive, "configured", False):
            report["check"] = await verify(suite.massive, market, now)
        return report
    try:
        return await _job(market, JOB, now, work)
    except DeskError as exc:
        return {"ok": False, "error": str(exc)[:300]}


__all__ = ["closing_bar", "collect", "due", "job", "target_session", "verify"]
