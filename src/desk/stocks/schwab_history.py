"""Earlier daily prices from Schwab's price history, so the rebuild and the replay reach past Massive's two years.

Massive's free plan keeps two years of daily bars. Schwab (when connected) returns up to 20 years of daily candles for
a stock in one request; ``price_history_years`` (default 10, docs/STOCKS_METHOD.md §9) says how many are kept. This
background job stores them in ``stk_bars`` for the days before the first stored Massive day only, never on or after
it, so a Massive row is never overwritten and every stored day has one source: those days are labelled
``schwab_pricehistory`` in ``stk_bar_days`` and each stock's check is kept in ``stk_history_symbols``.

Stored bars are unadjusted (splits are applied when reading). Whether Schwab's candles are split-adjusted is checked per
stock, never assumed:

* Over the sessions both sources have, Schwab's closes must match Massive's unadjusted closes either as they are or
  divided by the stored splits since each day (split-adjusted). Volumes must agree on the same basis.
* When no split falls in the overlap the two readings agree there, so each earlier split must show the basis in
  Schwab's own candles: an overnight gap of the split ratio when unadjusted, none when adjusted. A split too small to
  show (under 3-for-2) or a disagreement refuses the stock.
* Split-adjusted candles are un-adjusted with the stored split record, and candles older than that record (the
  ``corporate_history`` backfill) are not stored. A stock that doesn't reconcile is refused with its note.

Order: the benchmarks, then the research universe by recent dollar volume. A pass checks at most ``PER_PASS`` stocks
through the desk's shared Schwab limiter. The earlier years count in evaluations only once every queued stock has been
checked, so a half-filled year never enters a rebuild with a fraction of the universe. Companies delisted before
collection began have no free price source, so the Schwab years are not survivorship-free (docs/STOCKS_METHOD.md).
"""

from __future__ import annotations

import json
import math
import sqlite3
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from desk.core.clock import to_iso
from desk.core.config import StocksSection
from desk.core.errors import DeskError, ProviderError, SchemaError
from desk.stocks import calendar as cal
from desk.stocks.collect import _job, missing_price_days
from desk.stocks.contracts import Bar, Split
from desk.stocks.repo import StocksMarket, date_of, day_of
from desk.stocks.suite import StocksSuite
from desk.stocks.universe import BENCHMARKS, research_listings
from desk.storage.db import transaction

JOB = "schwab_history"
SOURCE = "schwab_pricehistory"
OFFICIAL = "massive"      # the overlap is checked only against Massive's own unadjusted bars
MAX_YEARS = 20                 # Schwab's longest daily history; price_history_years picks how much is kept
MIN_OVERLAP = 20               # shared sessions needed to check a stock
CLOSE_TOLERANCE = 0.01         # a Schwab close within 1% of the expected one matches
MIN_MATCH = 0.95               # of the shared sessions
VOLUME_RANGE = (0.6, 1.6)      # median Schwab/Massive volume on the same basis (a 2-for-1 basis error is 2.0)
DISTINCT_RATIO = 1.5           # an earlier split this large shows its basis in the candles' overnight gap
PER_PASS = 40                  # stocks a pass (under half a minute of the shared limiter)
RETRY = timedelta(days=1)      # a stock whose request failed is asked again after this


@dataclass
class Checked:
    status: str                       # stored | refused | none
    basis: str | None
    overlap: int
    note: str
    bars: list[Bar] = field(default_factory=list)


def boundary(conn: sqlite3.Connection) -> date | None:
    """The first day stored from any source but Schwab's history: Schwab rows go only before it."""
    row = conn.execute("SELECT MIN(day) FROM stk_bar_days WHERE source != ?", (SOURCE,)).fetchone()
    return date_of(row[0]) if row and row[0] is not None else None


def _detail(conn: sqlite3.Connection, name: str = JOB) -> dict[str, Any]:
    row = conn.execute("SELECT detail_json FROM stk_jobs WHERE name = ?", (name,)).fetchone()   # any row factory
    value = json.loads(row[0]) if row and row[0] else {}
    return value if isinstance(value, dict) else {}


def usable_from(conn: sqlite3.Connection) -> date | None:
    """The first day evaluations may read: Massive's first day until every queued stock's earlier history has been
    checked once, then no limit."""
    if _detail(conn).get("complete_at"):
        return None
    if conn.execute("SELECT 1 FROM stk_bar_days WHERE source = ? LIMIT 1", (SOURCE,)).fetchone() is None:
        return None
    return boundary(conn)


def queue(market: StocksMarket) -> list[str]:
    """The benchmarks, then the research universe by average dollar volume over the last 20 stored sessions."""
    recent = [r[0] for r in market.conn.execute("SELECT day FROM stk_bar_days WHERE rows > 0 ORDER BY day DESC LIMIT 20")]
    liquidity: dict[str, float] = {}
    if recent:
        liquidity = {r[0]: float(r[1] or 0) for r in market.conn.execute(
            "SELECT symbol, AVG(close * volume) FROM stk_bars WHERE day >= ? GROUP BY symbol", (min(recent),))}
    names = sorted({r["symbol"] for r in research_listings(market)}, key=lambda s: (-liquidity.get(s, 0.0), s))
    return list(dict.fromkeys([*BENCHMARKS, *names]))


def _gap_basis(candles: Sequence[Bar], execution: date, ratio: float) -> str | None:
    """Which basis the candles show around an earlier split: a gap of the ratio (unadjusted) or none (adjusted)."""
    if max(ratio, 1 / ratio) < DISTINCT_RATIO:
        return None
    before = [c for c in candles if c.day < execution]
    after = [c for c in candles if c.day >= execution]
    if not before or not after or (after[0].day - before[-1].day).days > 7:
        return None
    gap = math.log(before[-1].close / after[0].open)
    unadjusted, adjusted = abs(gap - math.log(ratio)), abs(gap)
    if min(unadjusted, adjusted) > abs(math.log(ratio)) / 3:
        return None
    return "unadjusted" if unadjusted < adjusted else "split-adjusted"


def reconcile(symbol: str, candles: Sequence[Bar], official: dict[date, Bar], splits: Sequence[Split], *,
              edge: date, record_from: date, today: date) -> Checked:
    """Check Schwab's candles against Massive's unadjusted bars and the split record; return what may be stored."""
    ratios = [(s.execution_date, s.ratio) for s in splits
              if s.symbol == symbol and s.split_from > 0 and s.split_to > 0 and s.execution_date <= today]

    def factor(d: date) -> float:
        return math.prod(r for e, r in ratios if e > d)

    shared = [(c, official[c.day]) for c in candles if c.day in official]
    earlier = [c for c in candles if c.day < edge]
    if not earlier:
        return Checked("none", None, len(shared), f"No Schwab candles before {edge.isoformat()}")
    if len(shared) < MIN_OVERLAP:
        return Checked("refused", None, len(shared), f"Only {len(shared)} sessions overlap Massive's stored bars "
                                                      f"({MIN_OVERLAP} needed to check the price basis)")
    fits: dict[str, float] = {}
    for basis in ("split-adjusted", "unadjusted"):
        errors = [abs(c.close * (factor(c.day) if basis == "split-adjusted" else 1) / m.close - 1) for c, m in shared]
        fits[basis] = sum(e <= CLOSE_TOLERANCE for e in errors) / len(errors)
    matching = {b for b, share in fits.items() if share >= MIN_MATCH}
    if not matching:
        best = max(fits.values())
        return Checked("refused", None, len(shared), f"Schwab's closes match Massive's on only {best:.0%} of "
                                                      f"{len(shared)} shared sessions on either basis")
    older = [(e, r) for e, r in ratios if earlier[0].day < e <= edge]
    shown = {b for e, r in older if (b := _gap_basis(candles, e, r)) is not None}
    if len(matching) == 1:
        basis = next(iter(matching))
        if shown - {basis}:
            return Checked("refused", None, len(shared), f"Schwab's candles around an earlier split disagree with the "
                                                          f"{basis} basis found on {len(shared)} shared sessions")
        how = f"proved on {len(shared)} shared sessions"
    elif not older:
        basis, how = "no splits", f"no split since its first candle; {len(shared)} shared sessions match"
    elif len(shown) == 1:
        basis = next(iter(shown))
        how = f"read from the gap at each earlier split; {len(shared)} shared sessions match"
    else:
        what = "disagree" if len(shown) > 1 else "are too small to show it"
        return Checked("refused", None, len(shared), f"No split in the overlap with Massive and the earlier splits "
                                                      f"{what}, so the basis of Schwab's older candles is unknown")
    adjusted = basis == "split-adjusted"
    volume = [c.volume / (factor(c.day) if adjusted else 1) / m.volume for c, m in shared if m.volume > 0 and c.volume > 0]
    if volume and not VOLUME_RANGE[0] <= statistics.median(volume) <= VOLUME_RANGE[1]:
        return Checked("refused", None, len(shared), f"Schwab's volumes are {statistics.median(volume):.2f}× Massive's "
                                                      f"on {len(volume)} shared sessions")
    bars = []
    for c in earlier:
        if c.day < record_from or not cal.is_trading_day(c.day):
            continue
        f = factor(c.day) if adjusted else 1.0
        bars.append(Bar(symbol, c.day, c.open * f, c.high * f, c.low * f, c.close * f, c.volume / f))
    if not bars:
        return Checked("none", basis, len(shared), f"No Schwab candles between {record_from.isoformat()} (the split "
                                                    f"record's start) and {edge.isoformat()}")
    return Checked("stored", basis, len(shared), f"{len(bars)} sessions {bars[0].day.isoformat()} to "
                                                 f"{bars[-1].day.isoformat()}; Schwab's candles {basis} ({how})", bars)


def save(conn: sqlite3.Connection, symbol: str, checked: Checked, now: datetime, *, years: int = MAX_YEARS) -> int:
    """Insert-once rows before the boundary, each day labelled with this source; one ledger row per stock."""
    stored = 0
    with transaction(conn):
        for b in checked.bars:
            inserted = conn.execute(
                "INSERT OR IGNORE INTO stk_bars(symbol, day, open, high, low, close, volume, vwap, trades) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
                (symbol, day_of(b.day), b.open, b.high, b.low, b.close, b.volume)).rowcount
            if inserted:
                stored += 1
                conn.execute("INSERT INTO stk_bar_days(day, source, rows, received_at) VALUES (?, ?, 1, ?) "
                             "ON CONFLICT(day) DO UPDATE SET rows = rows + 1 WHERE source = excluded.source",
                             (day_of(b.day), SOURCE, to_iso(now)))
        days = [b.day for b in checked.bars]
        conn.execute(
            "INSERT INTO stk_history_symbols(symbol, source, status, basis, overlap, first_day, last_day, rows, years, "
            "note, checked_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(symbol) DO UPDATE SET "
            "status=excluded.status, basis=excluded.basis, overlap=excluded.overlap, first_day=excluded.first_day, "
            "last_day=excluded.last_day, rows=excluded.rows, years=excluded.years, note=excluded.note, "
            "checked_at=excluded.checked_at",
            (symbol, SOURCE, checked.status, checked.basis, checked.overlap, day_of(days[0]) if days else None,
             day_of(days[-1]) if days else None, len(days), years, checked.note[:300], to_iso(now)))
    return stored


def _failed(conn: sqlite3.Connection, symbol: str, note: str, now: datetime) -> None:
    conn.execute("INSERT INTO stk_history_symbols(symbol, source, status, note, checked_at) VALUES (?, ?, 'failed', ?, ?) "
                 "ON CONFLICT(symbol) DO UPDATE SET status='failed', note=excluded.note, checked_at=excluded.checked_at",
                 (symbol, SOURCE, note[:300], to_iso(now)))


def waiting(market: StocksMarket, now: datetime, settings: StocksSection) -> str | None:
    """Why the job can't start yet: Massive's two years first (they fix the boundary), then the split record."""
    if boundary(market.conn) is None:
        return "Waiting for Massive's daily bars, which set where the earlier history ends"
    missing = missing_price_days(market, now, settings.history_days,
                                 timedelta(minutes=settings.refresh_after_close_minutes))
    if any((now.date() - d).days > 10 for d in missing):
        return "Waiting for Massive's two years of daily bars to finish downloading"
    corporate = _detail(market.conn, "corporate_history")
    if not corporate.get("complete"):
        return "Waiting for the split and dividend record that un-adjusts older prices"
    return None


async def job(suite: StocksSuite, market: StocksMarket, now: datetime, settings: StocksSection, *,
              per_pass: int = PER_PASS) -> dict[str, Any]:
    async def work() -> dict[str, Any]:
        client = suite.schwab
        if client is None or not client.configured:
            raise DeskError("Schwab is not connected; earlier price history waits for it")
        previous = _detail(market.conn)
        reason = waiting(market, now, settings)
        if reason:
            return {**coverage(market.conn), "waiting": reason, "remaining": 1}   # checked again next pass (no requests)
        edge = boundary(market.conn)
        assert edge is not None
        today = cal.new_york(now).date()
        corporate = _detail(market.conn, "corporate_history")
        years = min(MAX_YEARS, settings.price_history_years)
        record_from = max(date.fromisoformat(corporate["from"]), today - timedelta(days=365 * years + 5))
        # Unchecked stocks, failed ones after a day, and every stock checked for a shorter span than now asked for.
        checked = {r[0]: (r[1], r[2], r[3]) for r in market.conn.execute(
            "SELECT symbol, status, checked_at, years FROM stk_history_symbols")}
        todo = [s for s in queue(market) if s not in checked or (
            checked[s][0] == "failed" and now - datetime.fromisoformat(checked[s][1]) >= RETRY)
            or (checked[s][0] in ("stored", "none") and checked[s][2] < years)]
        splits = market.splits()
        rows = asked = 0
        for symbol in todo[:per_pass]:
            asked += 1
            try:
                candles = await client.price_history(symbol, now)
            except SchemaError as exc:
                _failed(market.conn, symbol, str(exc), now)
                continue
            except ProviderError as exc:
                if exc.status_code == 401:
                    raise
                _failed(market.conn, symbol, f"Schwab price history: HTTP {exc.status_code or 'error'}", now)
                continue
            official = {date_of(r[0]): Bar(symbol, date_of(r[0]), r[1], r[2], r[3], r[4], r[5]) for r in market.conn.execute(
                "SELECT b.day, b.open, b.high, b.low, b.close, b.volume FROM stk_bars b JOIN stk_bar_days d ON d.day = b.day "
                "WHERE b.symbol = ? AND b.day >= ? AND d.source = ?", (symbol, day_of(edge), OFFICIAL))}
            rows += save(market.conn, symbol, reconcile(symbol, candles, official, splits, edge=edge,
                                                        record_from=record_from, today=today), now, years=years)
        remaining = max(0, len(todo) - asked)
        done = previous.get("complete_at") or (to_iso(now) if remaining == 0 else None)
        return {**coverage(market.conn), "asked": asked, "rows": rows, "remaining": remaining, "complete_at": done}
    return await _job(market, JOB, now, work)


def coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    """What the earlier history holds: where Massive begins, how far Schwab's rows reach, and every stock's outcome."""
    edge = boundary(conn)
    counts = dict(conn.execute("SELECT status, COUNT(*) FROM stk_history_symbols GROUP BY status").fetchall())
    first = conn.execute("SELECT MIN(day) FROM stk_bar_days WHERE source = ?", (SOURCE,)).fetchone()[0]
    return {"massive_from": edge.isoformat() if edge else None,
            "schwab_from": date_of(first).isoformat() if first else None,
            "stored": counts.get("stored", 0), "refused": counts.get("refused", 0), "none": counts.get("none", 0),
            "failed": counts.get("failed", 0), "complete": bool(_detail(conn).get("complete_at"))}


__all__ = ["JOB", "SOURCE", "Checked", "boundary", "coverage", "job", "queue", "reconcile", "save", "usable_from",
           "waiting"]
