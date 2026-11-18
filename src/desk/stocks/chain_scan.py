"""The after-close chain scan: option signals for the ~500 most liquid optionable US stocks and ETFs every session
(docs/STOCKS_SOURCES.md → Chain scan; docs/STOCKS_METHOD.md → Options desk).

The daily option collection (``collect.option_chains``) covers a rotating focus list of about 30 names a pass, so a
cross-sectional desk would see a different few dozen stocks each day. This scan reads one compact chain per stock once
the session has closed, through Schwab's read-only market data and the desk's shared Schwab rate limiter:

* **Who**: active US stocks (common and ADR) and ETFs priced at least ``MIN_PRICE``, ranked by average dollar volume over
  the last ``RANK_SESSIONS`` stored sessions, the top ``NAMES``; a stock whose chain came back empty is left out for
  ``NO_OPTIONS_DAYS`` days.
* **What**: one quote request per batch of up to ``QUOTE_BATCH`` stocks (``quote`` and ``reference``: the borrow rate
  and hard-to-borrow flag, recorded from here on), then one chains request per stock: every expiry ``WINDOW`` days
  out, the ``STRIKES`` strikes nearest the money. Its measures go into ``stk_option_signals`` (source ``scan``) with
  the compact per-expiry snapshot; the raw chain is not kept (Plays and the archive keep their own collected chains,
  inside their own disk budget).
* **When and how much**: from ``START_AFTER`` past the close until the next session opens (a chain quoted later is not
  that session's), at most ``PASS_REQUESTS`` requests and ``PASS_SECONDS`` a collection pass; the next pass resumes
  where it stopped (``stk_option_scan`` records each stock's outcome). Schwab not connected: skipped, with the reason.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from desk.core.clock import to_iso
from desk.core.errors import DeskError, ProviderError
from desk.stocks import calendar as cal
from desk.stocks import option_signals as osg
from desk.storage.db import transaction

JOB = "chain_scan"
NAMES = 500
MIN_PRICE = 5.0
RANK_SESSIONS = 20
NO_OPTIONS_DAYS = 30
WINDOW = (8, 100)                 # days to expiry: 10-60 for the spread and skew, the expiries around 30 and 90
STRIKES = 20                      # strikes nearest the money per expiry (Schwab's strikeCount)
QUOTE_BATCH = 50
PASS_REQUESTS = 150               # under two minutes of Schwab's 90-a-minute pacing
PASS_SECONDS = 240.0
START_AFTER = timedelta(minutes=20)
KEEP_DAYS = 60
NOT_CONNECTED = "Schwab is not connected: the chain scan reads option chains through Schwab (connect it in Settings)"


def session_to_scan(now: datetime) -> date | None:
    """The closed session whose chains can still be read now: from ``START_AFTER`` past its close until the next
    session opens."""
    d = cal.last_closed_session(now, START_AFTER)
    opens = datetime.combine(cal.next_trading_day(d), datetime.min.time().replace(hour=9, minute=30), cal.NEW_YORK)
    return d if now < opens else None


def eligible(conn: Any, day: date, n: int = NAMES) -> list[str]:
    """The ``n`` most liquid stocks and ETFs as of ``day`` (stored bars up to it), less those without options lately."""
    sessions = cal.trading_days(day - timedelta(days=RANK_SESSIONS * 2), day)[-RANK_SESSIONS:]
    if not sessions:
        return []
    skip = {r[0] for r in conn.execute("SELECT DISTINCT symbol FROM stk_option_scan WHERE status='no_options' AND "
                                       "day>=?", ((day - timedelta(days=NO_OPTIONS_DAYS)).toordinal(),))}
    rows = conn.execute(
        "SELECT b.symbol, AVG(b.close * b.volume), AVG(b.close) FROM stk_bars b JOIN stk_listings l ON l.symbol=b.symbol "
        "WHERE b.day>=? AND b.day<=? AND l.active=1 AND l.test=0 AND (l.etf=1 OR l.kind IN ('common', 'adr')) "
        "GROUP BY b.symbol", (sessions[0].toordinal(), day.toordinal())).fetchall()
    ranked = sorted(((r[1] or 0.0, r[0]) for r in rows if r[0] not in skip and (r[2] or 0) >= MIN_PRICE),
                    key=lambda x: (-x[0], x[1]))
    return [s for _, s in ranked[:n]]


def outcomes(conn: Any, day: date) -> dict[str, str]:
    return {r[0]: r[1] for r in conn.execute("SELECT symbol, status FROM stk_option_scan WHERE day=?",
                                             (day.toordinal(),))}


def coverage(conn: Any, day: date | None, eligible_count: int | None = None) -> dict[str, Any]:
    """What the scan covered for ``day``: eligible, scanned (usable), thin, no options, failed, left."""
    if day is None:
        return {"session": None}
    got = outcomes(conn, day)
    counts = {k: sum(1 for v in got.values() if v == k) for k in ("scanned", "thin", "no_options", "failed")}
    eligible_count = len(eligible(conn, day)) if eligible_count is None else eligible_count
    return {"session": day.isoformat(), "eligible": eligible_count, **counts,
            "remaining": max(0, eligible_count - len(got))}


def _record(conn: Any, day: date, symbol: str, status: str, note: str | None, now: datetime) -> None:
    conn.execute("INSERT OR REPLACE INTO stk_option_scan VALUES (?,?,?,?,?)",
                 (day.toordinal(), symbol, status, note, to_iso(now)))


async def scan(client: Any, market: Any, now: datetime, *, names: int = NAMES, max_requests: int = PASS_REQUESTS,
               max_seconds: float = PASS_SECONDS, clock: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    """One bounded pass of the scan (module docstring). Returns its coverage, requests and why it stopped."""
    conn = market.conn
    day = session_to_scan(now)
    if day is None:
        return {"skipped": "no closed session to scan until the market closes", **coverage(conn, None)}
    wanted = eligible(conn, day, names)
    done = outcomes(conn, day)
    todo = [s for s in wanted if s not in done]
    started, used, stopped = clock(), 0, None
    pricing = osg.Pricing(market) if todo else None
    first, last = day + timedelta(days=WINDOW[0]), day + timedelta(days=WINDOW[1])
    while todo and stopped is None:
        batch, todo = todo[:QUOTE_BATCH], todo[QUOTE_BATCH:]
        if used >= max_requests or clock() - started > max_seconds:
            stopped = "pass budget"
            break
        try:
            quotes, details = await client.quote_details(batch)
        except ProviderError as exc:
            if exc.status_code == 401:
                raise
            quotes, details = [], {}
        used += 1
        last_price = {q.symbol: q for q in quotes}
        for i, symbol in enumerate(batch):
            if used >= max_requests or clock() - started > max_seconds:
                stopped = "pass budget"
                todo = batch[i:] + todo
                break
            used += 1
            try:
                chains, price = await client.chain_window(symbol, first, last, strikes=STRIKES)
            except ProviderError as exc:
                if exc.status_code == 401:
                    raise
                _record(conn, day, symbol, "failed", f"Schwab chains: HTTP {exc.status_code or 'error'}", now)
                continue
            except DeskError as exc:
                _record(conn, day, symbol, "failed", str(exc)[:160], now)
                continue
            q = last_price.get(symbol)
            price = price or (q.last if q is not None else None)
            if not any(chains.values()):
                _record(conn, day, symbol, "no_options", "no standard contracts listed in the window", now)
                continue
            if not price or price <= 0:
                _record(conn, day, symbol, "failed", "no stock price with the chain", now)
                continue
            assert pricing is not None
            m = osg.measure(chains, price, day, pricing.rate(day), pricing.yield_on(symbol, day, price))
            shares = osg.share_volume(conn, symbol, day) or (q.volume if q is not None and q.volume else None)
            with transaction(conn):
                osg.save(conn, symbol, day, "scan", m, price=price, stock_volume=shares,
                         borrow=osg.borrow_of(details.get(symbol)), rv22=osg.realized(conn, symbol, day),
                         observed_at=now, now=now)
                _record(conn, day, symbol, "scanned" if m["liquid"] >= osg.MIN_LIQUID else "thin",
                        None if m["liquid"] >= osg.MIN_LIQUID else f"{m['liquid']} liquid contracts", now)
    conn.execute("DELETE FROM stk_option_scan WHERE day<?", ((day - timedelta(days=KEEP_DAYS)).toordinal(),))
    out = {"requests": used, **coverage(conn, day, len(wanted))}
    if stopped and out["remaining"]:
        out["stopped"] = stopped
    return out


def due(market: Any, now: datetime) -> bool:
    """A session is open for scanning and the last pass did not finish it."""
    day = session_to_scan(now)
    if day is None:
        return False
    detail = (market.jobs().get(JOB) or {}).get("detail") or {}
    return detail.get("session") != day.isoformat() or bool(detail.get("remaining"))


async def job(suite: Any, market: Any, now: datetime, *, progress: Callable[[str], None] | None = None,
              **limits: Any) -> dict[str, Any]:
    """The collection worker's step, recorded as the ``chain_scan`` job (a skip is recorded, not an error)."""
    client = getattr(suite, "schwab", None)
    if client is None or not client.configured:
        day = session_to_scan(now)
        out: dict[str, Any] = {"skipped": NOT_CONNECTED, "session": day.isoformat() if day else None, "remaining": 0}
        market.job(JOB, now, detail=out)
        return {"ok": True, **out}
    try:
        out = await scan(client, market, now, **limits)
    except DeskError as exc:
        market.job(JOB, now, error=str(exc)[:300])
        return {"ok": False, "error": str(exc)[:300]}
    market.job(JOB, now, detail=out)
    return {"ok": True, **out}


def summary(market: Any) -> dict[str, Any]:
    """The newest pass's coverage for Evidence → Data health."""
    entry = market.jobs().get(JOB) or {}
    detail = entry.get("detail") or {}
    return {**detail, "last_run_at": entry.get("last_run_at"), "error": entry.get("last_error")}


__all__ = ["JOB", "NAMES", "NOT_CONNECTED", "coverage", "due", "eligible", "job", "outcomes", "scan",
           "session_to_scan", "summary"]
