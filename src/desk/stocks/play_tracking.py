"""Plays history: follow every recorded play after it was picked (docs/STOCKS_PLAYS.md → History). Research only.

**Live tracking.** The tracked set is every leg of every unexpired recorded play, the legs of today's live board and
the twins (random matched contracts, the baseline every play must beat). During market hours a pass every five
minutes quotes what is due from Schwab's ``/quotes`` endpoint, 100 option symbols a request, with the provider's own
IV and greeks; contracts it does not return fall back to the selected provider's per-expiry chains, and anything the
research collector fetched since the last pass is used for free. A pass spends at most ``REQUESTS_PER_PASS``
requests, so tracking takes under a tenth of the shared 90-a-minute Schwab budget, and contracts are served in
priority order: the live board, plays from the last five sessions and anything expiring within a week every pass;
older plays every 15 minutes; twins hourly. From 15:50 New York time every contract is due once more for its
closing quote.

Streaming level-one quotes are not used: the streamer needs Schwab's account-scoped user preferences, and this
integration deliberately requests market data only. Five-minute REST passes meet the five-minute requirement.

**At the close** the session's quotes are folded into one packed intraday row per contract (kept at least 30 days;
the database refuses an earlier delete) and one permanent daily row with the closing bid/ask, Schwab's IV and greeks
beside KESTREL's own (solved from the mid), and the day's high and low mid. The working quote table is then cleared.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import zlib
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Protocol

from desk.core.clock import to_iso, utc_now
from desk.core.config import LoadedConfig
from desk.core.errors import DeskError, ProviderError
from desk.core.paths import DeskPaths
from desk.stocks import calendar as cal
from desk.stocks import options as opt
from desk.stocks.contracts import OptionQuote, OptionTick, StockQuote
from desk.stocks.repo import MARKET_FILE, RESEARCH_FILE, day_of
from desk.stocks.sources.schwab import OCC
from desk.storage.db import DbKind, connect, transaction

log = logging.getLogger(__name__)

VERSION = 1
PASS_MINUTES = 5
REQUESTS_PER_PASS = 40              # ≤ 8 a minute on average: under a tenth of the 90-a-minute Schwab budget
QUOTE_BATCH = 100
PRIORITY_SESSIONS = 5               # plays from the last five sessions are quoted every pass
SHORT_DATED_DAYS = 7
INTERVALS = {0: timedelta(minutes=5), 1: timedelta(minutes=15), 2: timedelta(minutes=60)}
PRIORITY_LABELS = {0: "every 5 minutes (live board, last 5 sessions, expiring within a week)",
                   1: "every 15 minutes (older plays)", 2: "hourly (twins: the random baseline)"}
OPEN = time(9, 30)
CLOSE_WINDOW = time(15, 50)         # every contract is due once more for its closing quote
FOLD_AFTER = timedelta(minutes=5)   # a session's quotes are folded five minutes after the close
FRESH_CLOSE = timedelta(minutes=60)  # a closing row needs a quote from the session's last hour
INTRADAY_KEEP_DAYS = 35             # compacted after this (the database refuses deletes inside 30 days)
PASS_TIMEOUT = 240.0                # a pass never runs into the next one


# ── contracts ────────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Tracked:
    occ: str
    symbol: str
    kind: str
    strike: float
    expiration: date
    priority: int                    # 0, 1 or 2 (PRIORITY_LABELS)


def parse_occ(occ: str) -> tuple[str, str, float, date]:
    """``AAPL261120C00250000`` → (AAPL, call, 250.0, 2026-11-20)."""
    if not OCC.fullmatch(occ):
        raise ValueError(f"not an OCC option symbol: {occ!r}")
    tail = occ[-15:]
    expiration = date(2000 + int(tail[0:2]), int(tail[2:4]), int(tail[4:6]))
    return occ[:-15], "call" if tail[6] == "C" else "put", int(tail[7:]) / 1000, expiration


def legs_key(legs: Iterable[dict[str, Any]]) -> str:
    """A play's identity independent of its rank: its contracts and sides."""
    return ",".join(sorted(f"{leg['occ']}:{leg['side']}" for leg in legs))


def _recent_cutoff(today: date) -> date:
    d = today
    for _ in range(PRIORITY_SESSIONS):
        d = cal.previous_trading_day(d)
    return d


def tracked_set(research: sqlite3.Connection, today: date, board: dict[str, Any] | None) -> dict[str, Tracked]:
    """Every leg to follow today, at its most urgent priority."""
    wanted: dict[str, int] = {}

    def want(occ: str, priority: int) -> None:
        if OCC.fullmatch(occ) and parse_occ(occ)[3] >= today:
            wanted[occ] = min(priority, wanted.get(occ, 9))

    cutoff, soon = day_of(_recent_cutoff(today)), day_of(today + timedelta(days=SHORT_DATED_DAYS))
    for r in research.execute("SELECT session, expiration, legs_json FROM stk_plays WHERE expiration >= ?",
                              (day_of(today),)):
        priority = 0 if r["session"] >= cutoff or r["expiration"] <= soon else 1
        for leg in json.loads(r["legs_json"]):
            want(leg["occ"], priority)
    for r in research.execute("SELECT legs_json FROM stk_play_twins WHERE expiration >= ?", (day_of(today),)):
        for leg in json.loads(r["legs_json"]):
            want(leg["occ"], 2)
    for category in (board or {}).get("categories") or []:
        for play in [*(category.get("plays") or []), *(category.get("budget_plays") or [])]:
            for leg in play.get("legs") or []:
                want(str(leg.get("occ", "")), 0)
    from desk.stocks.play_selections import active_legs

    for leg in active_legs(research, today):
        want(leg["occ"], 0)
    # Every automatically archived idea stays in rotation, even after it leaves today's board.
    for row in research.execute("SELECT session,play_json FROM stk_play_ideas "
                                "WHERE json_extract(play_json,'$.expiration')>=?", (today.isoformat(),)):
        play = json.loads(row["play_json"])
        priority = 0 if row["session"] >= cutoff or play["expiration"] <= date.fromordinal(soon).isoformat() else 1
        for leg in play["legs"]:
            want(leg["occ"], priority)
    out = {}
    for occ, priority in wanted.items():
        symbol, kind, strike, expiration = parse_occ(occ)
        out[occ] = Tracked(occ, symbol, kind, strike, expiration, priority)
    return out


def market_open(now: datetime) -> bool:
    local = cal.new_york(now)
    return cal.is_trading_day(local.date()) and OPEN <= local.time() <= time(16, 1)


def due(tracked: dict[str, Tracked], last: dict[str, datetime], now: datetime) -> list[Tracked]:
    """What this pass should quote, most urgent and stalest first."""
    local = cal.new_york(now)
    closing = local.time() >= CLOSE_WINDOW
    close_from = datetime.combine(local.date(), CLOSE_WINDOW, cal.NEW_YORK)
    out = []
    for t in tracked.values():
        seen = last.get(t.occ)
        if seen is None or now - seen >= INTERVALS[t.priority] - timedelta(seconds=20) or (closing and seen < close_from):
            out.append(t)
    epoch = datetime.min.replace(tzinfo=now.tzinfo)
    return sorted(out, key=lambda t: (t.priority, last.get(t.occ, epoch), t.occ))


# ── quotes ───────────────────────────────────────────────────────────────────────────────────────
class QuoteClient(Protocol):
    @property
    def configured(self) -> bool: ...
    async def option_quotes(self, occs: list[str]) -> list[OptionTick]: ...


class ChainClient(Protocol):
    @property
    def configured(self) -> bool: ...
    async def chain(self, symbol: str, expiration: date) -> list[OptionQuote]: ...
    async def quotes(self, symbols: list[str]) -> list[StockQuote]: ...


@dataclass
class PassResult:
    requests: int = 0
    quoted: int = 0
    sources: Counter[str] = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    endpoint: str | None = None      # "working" | "no_options" | "refused" when the quotes endpoint was tried


Save = Callable[[list[OptionTick], str], None]


def _from_chain(q: OptionQuote, underlying: float | None) -> OptionTick:
    return OptionTick(q.occ, q.bid, q.ask, q.last, None, q.volume, q.open_interest, None, None, None, None, None,
                      underlying, q.quoted_at)


async def quote_pass(due_now: Sequence[Tracked], save: Save, *, schwab: QuoteClient | None, chains: ChainClient | None,
                     budget: int = REQUESTS_PER_PASS, use_quotes: bool = True) -> PassResult:
    """Quote ``due_now`` within ``budget`` requests: Schwab option quotes first, then per-expiry chains."""
    result = PassResult()
    pending = [t.occ for t in due_now]
    by_occ = {t.occ: t for t in due_now}
    got: set[str] = set()
    if use_quotes and schwab is not None and schwab.configured:
        for start in range(0, len(pending), QUOTE_BATCH):
            if result.requests >= budget:
                break
            batch = pending[start:start + QUOTE_BATCH]
            result.requests += 1
            try:
                ticks = await schwab.option_quotes(batch)
            except ProviderError as exc:
                result.errors.append(f"Schwab option quotes: {exc}"[:200])
                if exc.status_code in (400, 404) and start == 0:
                    result.endpoint = "refused"
                break                      # retried next pass; the chains below still run
            except DeskError as exc:       # a response that did not parse as option quotes
                result.errors.append(f"Schwab option quotes: {exc}"[:200])
                result.endpoint = result.endpoint or "no_options"
                break
            usable = [t for t in ticks if t.bid is not None and t.ask is not None and t.ask > 0]
            if usable:                     # an empty answer proves nothing (the contracts may not be listed)
                save(usable, "schwab_quotes")
                got.update(t.occ for t in usable)
                result.sources["schwab_quotes"] += len(usable)
                result.endpoint = "working"
    left = [occ for occ in pending if occ not in got]
    if left and chains is not None and chains.configured:
        groups: dict[tuple[str, date], list[str]] = defaultdict(list)
        for occ in left:
            t = by_occ[occ]
            groups[(t.symbol, t.expiration)].append(occ)
        order = sorted(groups, key=lambda g: (min(by_occ[o].priority for o in groups[g]), -len(groups[g]), g))
        fetched: list[tuple[OptionQuote, str]] = []
        for symbol, expiration in order:
            if result.requests >= budget - 1:      # keep one request for the stock prices
                break
            result.requests += 1
            try:
                quotes = await chains.chain(symbol, expiration)
            except DeskError as exc:
                result.errors.append(f"{symbol} {expiration.isoformat()} chain: {exc}"[:200])
                continue
            wanted = set(groups[(symbol, expiration)])
            fetched.extend((q, symbol) for q in quotes if q.occ in wanted and q.bid is not None and q.ask is not None)
        prices: dict[str, float] = {}
        symbols = sorted({s for _, s in fetched})
        for start in range(0, len(symbols), 100):
            if result.requests >= budget:
                break
            result.requests += 1
            try:
                prices.update({q.symbol: q.last for q in await chains.quotes(symbols[start:start + 100])
                               if q.last is not None and q.last > 0})
            except DeskError as exc:
                result.errors.append(f"stock prices: {exc}"[:200])
        ticks = [_from_chain(q, prices.get(s)) for q, s in fetched]
        if ticks:
            save(ticks, "chains")
            got.update(t.occ for t in ticks)
            result.sources["chains"] += len(ticks)
    result.quoted = len(got)
    result.missing = [occ for occ in pending if occ not in got]
    return result


def tick_rows(ticks: Iterable[OptionTick], at: datetime, session: date, source: str) -> list[tuple[Any, ...]]:
    return [(t.occ, to_iso(at), day_of(session), t.bid, t.ask, t.last, t.mark, t.volume, t.open_interest, t.iv,
             t.delta, t.gamma, t.theta, t.vega, t.underlying_price, to_iso(t.quoted_at) if t.quoted_at else None, source)
            for t in ticks]


def save_ticks(research: sqlite3.Connection, rows: list[tuple[Any, ...]]) -> None:
    if rows:
        research.executemany(
            "INSERT OR IGNORE INTO stk_track_ticks(occ, at, session, bid, ask, last, mark, volume, open_interest,"
            " schwab_iv, schwab_delta, schwab_gamma, schwab_theta, schwab_vega, underlying, quoted_at, source)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)


def archive_ticks(market: sqlite3.Connection, tracked: dict[str, Tracked], session: date, after: str | None,
                  now: datetime) -> tuple[list[tuple[Any, ...]], str | None]:
    """Tracked contracts in chains the research collector stored since ``after`` and by ``now`` (no extra request)."""
    symbols = {t.symbol for t in tracked.values()}
    rows: list[tuple[Any, ...]] = []
    newest = after
    for r in market.execute("SELECT underlying, observed_at, underlying_price, chain_zlib, source FROM stk_option_chains"
                            " WHERE day = ? AND observed_at > ? AND observed_at <= ? ORDER BY observed_at",
                            (day_of(session), after or "", to_iso(now))):
        newest = max(newest or "", r["observed_at"])
        if r["underlying"] not in symbols:
            continue
        at = datetime.fromisoformat(r["observed_at"])
        for q in json.loads(zlib.decompress(r["chain_zlib"])):
            if q[0] in tracked and q[3] is not None and q[4] is not None:
                tick = OptionTick(q[0], q[3], q[4], q[5], None, q[6], q[7], None, None, None, None, None,
                                  r["underlying_price"], datetime.fromisoformat(q[9]) if q[9] else None)
                rows.extend(tick_rows([tick], at, session, f"collector:{r['source']}"))
    return rows, newest


def last_ticks(research: sqlite3.Connection, session: date) -> dict[str, datetime]:
    return {r["occ"]: datetime.fromisoformat(r["at"]) for r in research.execute(
        "SELECT occ, MAX(at) AS at FROM stk_track_ticks WHERE session = ? GROUP BY occ", (day_of(session),))}


# ── the fold at the close ────────────────────────────────────────────────────────────────────────
def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None or not math.isfinite(value) else round(value, digits)


def pack(points: list[list[float | None]]) -> bytes:
    return zlib.compress(json.dumps(points, allow_nan=False, separators=(",", ":")).encode(), 9)


def unpack(blob: bytes) -> list[list[float | None]]:
    value = json.loads(zlib.decompress(blob))
    return value if isinstance(value, list) else []


def computed_greeks(kind: str, mid: float | None, s: float | None, strike: float, expiration: date, session: date,
                    rate: float) -> dict[str, float | None]:
    """KESTREL's IV and greeks from a closing mid (Black-Scholes, no dividends), as Plays computes them."""
    empty: dict[str, float | None] = {"iv": None, "delta": None, "gamma": None, "theta": None, "vega": None}
    if mid is None or s is None or mid <= 0 or s <= 0:
        return empty
    t = max((expiration - session).days, 0.25) / opt.YEAR_DAYS
    iv = opt.implied_vol(kind, mid, s, strike, t, rate)
    if iv is None:
        return empty
    g = opt.greeks(kind, s, strike, t, iv, rate)
    return {"iv": _round(iv), "delta": _round(g.delta), "gamma": _round(g.gamma, 5), "theta": _round(g.theta_per_day),
            "vega": _round(g.vega_per_point)}


def fold(research: sqlite3.Connection, session: date, now: datetime, rate: Callable[[date], float]) -> int:
    """Pack one session's quotes into permanent rows, then clear them. Returns the contracts folded."""
    by_occ: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in research.execute("SELECT * FROM stk_track_ticks WHERE session = ? ORDER BY occ, at", (day_of(session),)):
        by_occ[r["occ"]].append(r)
    close = cal.session_close(session)
    open_minute, close_minute = OPEN.hour * 60 + OPEN.minute, 16 * 60
    r_session = rate(session)
    intraday, days = [], []
    for occ, rows in by_occ.items():
        _, kind, strike, expiration = parse_occ(occ)
        points: dict[int, list[float | None]] = {}
        mids: list[float] = []
        closing = None
        for r in rows:
            at = datetime.fromisoformat(r["at"])
            local = cal.new_york(at)
            minute = local.hour * 60 + local.minute
            if local.date() != session or not open_minute <= minute <= close_minute + 2:
                continue
            if r["bid"] is None or r["ask"] is None or r["ask"] <= 0 or r["bid"] > r["ask"]:
                continue
            points[min(minute, close_minute)] = [min(minute, close_minute), r["bid"], r["ask"],
                                                 _round(r["underlying"], 2), _round(r["schwab_iv"], 4)]
            mids.append((r["bid"] + r["ask"]) / 2)
            if at <= close + timedelta(minutes=2):
                closing = r
        if points:
            ordered = [points[m] for m in sorted(points)]
            intraday.append((occ, day_of(session), "quoted", len(ordered), pack(ordered), to_iso(now)))
        if closing is None or datetime.fromisoformat(closing["at"]) < close - FRESH_CLOSE:
            continue                       # no quote in the last hour: the marks step models this day instead
        mid = (closing["bid"] + closing["ask"]) / 2
        g = computed_greeks(kind, mid, closing["underlying"], strike, expiration, session, r_session)
        days.append((occ, day_of(session), "quoted", closing["bid"], closing["ask"], closing["mark"], closing["last"],
                     closing["volume"], closing["open_interest"], closing["underlying"], g["iv"], g["delta"],
                     g["gamma"], g["theta"], g["vega"], closing["schwab_iv"], closing["schwab_delta"],
                     closing["schwab_gamma"], closing["schwab_theta"], closing["schwab_vega"],
                     _round(max(mids)) if mids else None, _round(min(mids)) if mids else None, closing["quoted_at"],
                     closing["source"], None, to_iso(now)))
    with transaction(research):
        research.executemany("INSERT OR IGNORE INTO stk_track_intraday(occ, session, status, points, packed, folded_at)"
                             " VALUES (?, ?, ?, ?, ?, ?)", intraday)
        research.executemany(
            "INSERT OR IGNORE INTO stk_track_days(occ, session, status, bid, ask, mark, last, volume, open_interest,"
            " underlying, iv, delta, gamma, theta, vega, schwab_iv, schwab_delta, schwab_gamma, schwab_theta,"
            " schwab_vega, high_mid, low_mid, quoted_at, source, note, recorded_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", days)
        research.execute("DELETE FROM stk_track_ticks WHERE session = ?", (day_of(session),))
    return len(by_occ)


def fold_due(research: sqlite3.Connection, now: datetime, rate: Callable[[date], float]) -> list[date]:
    """Fold every session whose close has passed (including ones left behind while the laptop was off)."""
    done = []
    for r in research.execute("SELECT DISTINCT session FROM stk_track_ticks ORDER BY session").fetchall():
        session = date.fromordinal(r["session"])
        if now >= cal.session_close(session) + FOLD_AFTER:
            fold(research, session, now, rate)
            done.append(session)
    return done


def compact(research: sqlite3.Connection, today: date) -> int:
    """Drop intraday detail older than ``INTRADAY_KEEP_DAYS``; the daily rows keep each day's close, high and low."""
    cur = research.execute("DELETE FROM stk_track_intraday WHERE session < ?", (day_of(today) - INTRADAY_KEEP_DAYS,))
    return cur.rowcount


# ── first sightings ──────────────────────────────────────────────────────────────────────────────
def first_seen(research: sqlite3.Connection, board: dict[str, Any], now: datetime) -> int:
    """Record when each play first appeared on the live board during its session, with its quotes then. Later
    sightings change nothing; a board first built after the close records no sighting (the close is its entry)."""
    session = board.get("asof")
    if not session:
        return 0
    seen = datetime.fromisoformat(board["generated_at"]) if board.get("generated_at") else now
    if cal.new_york(seen).date().isoformat() != session or seen > cal.session_close(date.fromisoformat(session)):
        return 0
    rows = []
    for category in board.get("categories") or []:
        for p in [*(category.get("plays") or []), *(category.get("budget_plays") or [])]:
            legs = p.get("legs") or []
            if not legs or p.get("entry") is None:
                continue
            rows.append((day_of(date.fromisoformat(session)), category["id"], legs_key(legs), p["symbol"], to_iso(seen),
                         float(p["entry"]), json.dumps(legs, allow_nan=False, separators=(",", ":"))))
    with transaction(research):
        before = research.total_changes
        research.executemany("INSERT OR IGNORE INTO stk_play_first_seen(session, category, key, symbol, seen_at, entry,"
                             " legs_json) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        return research.total_changes - before


# ── state and status ─────────────────────────────────────────────────────────────────────────────
def get_state(research: sqlite3.Connection, key: str) -> dict[str, Any]:
    row = research.execute("SELECT value_json FROM stk_track_state WHERE key = ?", (key,)).fetchone()
    if row is None:
        return {}
    value = json.loads(row["value_json"])
    return value if isinstance(value, dict) else {}


def put_state(research: sqlite3.Connection, key: str, value: dict[str, Any], now: datetime) -> None:
    research.execute("INSERT INTO stk_track_state(key, value_json, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO "
                     "UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at",
                     (key, json.dumps(value, allow_nan=False, default=str), to_iso(now)))


def freshness(tracked: dict[str, Tracked], last: dict[str, datetime], now: datetime) -> dict[str, Any]:
    buckets: Counter[str] = Counter()
    late: list[tuple[int, float, Tracked]] = []
    for t in tracked.values():
        seen = last.get(t.occ)
        age = math.inf if seen is None else (now - seen).total_seconds() / 60
        bucket = ("never today" if seen is None else "≤ 5 min" if age <= 5.5 else "≤ 15 min" if age <= 15.5
                  else "≤ 60 min" if age <= 61 else "older")
        buckets[bucket] += 1
        if age > INTERVALS[t.priority].total_seconds() / 60 + 1:
            late.append((t.priority, age, t))
    late.sort(key=lambda x: (x[0], -x[1], x[2].occ))
    return {"buckets": dict(buckets), "waiting": len(late),
            "late": [{"occ": t.occ, "symbol": t.symbol, "priority": p, "minutes": None if math.isinf(a) else round(a, 1)}
                     for p, a, t in late[:25]]}


TABLES = ("stk_track_ticks", "stk_track_intraday", "stk_track_days", "stk_track_closes", "stk_play_marks",
          "stk_play_first_seen", "stk_play_twins", "stk_play_quarantine", "stk_play_learning")


def storage(research: sqlite3.Connection) -> dict[str, Any]:
    out: dict[str, Any] = {"rows": {}, "bytes": None}
    for name in TABLES:
        out["rows"][name] = research.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]  # noqa: S608 (fixed names)
    try:
        marks = ",".join("?" * len(TABLES))
        out["bytes"] = int(research.execute(f"SELECT COALESCE(SUM(pgsize), 0) FROM dbstat WHERE name IN ({marks})",  # noqa: S608
                                            TABLES).fetchone()[0])
    except sqlite3.Error:
        pass                                          # dbstat is not compiled into every SQLite
    return out


def status(research: sqlite3.Connection, now: datetime) -> dict[str, Any]:
    """What System shows: what is tracked, how fresh, what waits, what the endpoint did and the storage used."""
    try:
        state = get_state(research, "pass")
        endpoint = get_state(research, "endpoint")
        today = cal.new_york(now).date()
        ticks = research.execute("SELECT COUNT(*) n, COUNT(DISTINCT occ) c, MAX(at) last FROM stk_track_ticks "
                                 "WHERE session = ?", (day_of(today),)).fetchone()
        days = research.execute("SELECT MAX(session) FROM stk_track_days").fetchone()[0]
        return {"version": VERSION, "market_open": market_open(now), "pass": state, "endpoint": endpoint,
                "idle": get_state(research, "idle"),
                "repair": get_state(research, "repair"), "today": {"quotes": ticks["n"], "contracts": ticks["c"],
                                                                 "last_quote_at": ticks["last"]},
                "last_close_folded": date.fromordinal(days).isoformat() if days else None,
                "storage": storage(research), "priorities": PRIORITY_LABELS,
                "budget": {"requests_per_pass": REQUESTS_PER_PASS, "pass_minutes": PASS_MINUTES,
                           "schwab_limit_per_minute": 90}}
    except sqlite3.OperationalError:
        return {"version": VERSION, "waiting": "Play tracking starts after the dashboard restarts on this version"}


# ── verifying the endpoint on the operator's laptop ──────────────────────────────────────────────
async def probe(schwab: QuoteClient, chains: ChainClient, market: sqlite3.Connection, research: sqlite3.Connection,
                now: datetime) -> dict[str, Any]:
    """``desk stocks doctor --online``: ask Schwab's quotes endpoint for three SPY contracts and report exactly what
    came back (prices, IV, greeks, quote time). Saved as the endpoint's state so tracking uses what was verified."""
    if not schwab.configured:
        return {"checked": False, "reason": "Schwab is not connected"}
    row = market.execute("SELECT day, expiration, underlying_price, chain_zlib FROM stk_option_chains WHERE underlying "
                         "= 'SPY' ORDER BY day DESC, expiration LIMIT 1").fetchone()
    requests = 0
    if row is not None and row["underlying_price"]:
        price = float(row["underlying_price"])
        contracts = [q[0] for q in json.loads(zlib.decompress(row["chain_zlib"]))]
    else:
        if not chains.configured:
            return {"checked": False, "reason": "No stored SPY chain and no chain provider to pick contracts from"}
        expiration = next(d for d in [cal.new_york(now).date() + timedelta(days=i) for i in range(7, 40)]
                          if d.weekday() == 4)
        quotes = await chains.chain("SPY", expiration)
        prices = await chains.quotes(["SPY"])
        requests += 2
        if not quotes or not prices or prices[0].last is None:
            return {"checked": False, "reason": "Could not pick SPY contracts to probe", "requests": requests}
        price = float(prices[0].last)
        contracts = [q.occ for q in quotes]
    contracts = [o for o in contracts if parse_occ(o)[3] >= cal.new_york(now).date()]
    picked = sorted(contracts, key=lambda o: abs(parse_occ(o)[2] - price))[:3]
    if not picked:
        return {"checked": False, "reason": "The stored SPY chain has expired", "requests": requests}
    try:
        ticks = await schwab.option_quotes(picked)
        state = "working" if any(t.bid is not None and t.ask is not None for t in ticks) else "no_options"
        out: dict[str, Any] = {"checked": True, "state": state, "requested": picked, "returned": len(ticks),
                               "with_iv": sum(t.iv is not None for t in ticks),
                               "with_greeks": sum(t.delta is not None for t in ticks),
                               "sample": [{"occ": t.occ, "bid": t.bid, "ask": t.ask, "iv": t.iv, "delta": t.delta,
                                           "underlying": t.underlying_price,
                                           "quoted_at": to_iso(t.quoted_at) if t.quoted_at else None} for t in ticks]}
    except ProviderError as exc:
        state = "refused" if exc.status_code in (400, 404) else "error"
        out = {"checked": True, "state": state, "requested": picked, "error": str(exc)[:200]}
    except DeskError as exc:
        state = "no_options"
        out = {"checked": True, "state": state, "requested": picked, "error": str(exc)[:200]}
    out["requests"] = requests + 1
    out["meaning"] = {"working": "Plays tracking quotes 100 contracts a request with Schwab's IV and greeks.",
                      "no_options": "Schwab answered without option quotes; tracking falls back to per-expiry chains.",
                      "refused": "Schwab refused option symbols on /quotes; tracking falls back to per-expiry chains.",
                      "error": "The request failed; tracking retries each pass and falls back to chains meanwhile."}[state]
    if state != "error":
        with transaction(research):
            put_state(research, "endpoint", {"state": state, "day": cal.new_york(now).date().isoformat(),
                                             "at": to_iso(now), "probe": True}, now)
    return out


# ── one pass ─────────────────────────────────────────────────────────────────────────────────────
MARKS_EVERY = timedelta(hours=1)    # marks and gap repair: every pass after the close, hourly while trading


async def run_pass(market: sqlite3.Connection, research: sqlite3.Connection, now: datetime, *,
                   board: dict[str, Any] | None, schwab: QuoteClient | None, chains: ChainClient | None,
                   rate: Callable[[date], float], history: Any = None,
                   clock: Callable[[], datetime] = utc_now) -> dict[str, Any]:
    """Fold closed sessions, compact old detail, mark and repair plays and, in market hours, quote what is due."""
    from desk.stocks import play_marks
    from desk.stocks.play_archive import recover

    today = cal.new_york(now).date()
    recovered = recover(research,now)
    folded = fold_due(research, now, rate)
    compacted = compact(research, today)
    report: dict[str, Any] = {"at": to_iso(now), "folded": [d.isoformat() for d in folded], "compacted": compacted,
                              "archive_recovery":recovered}
    from desk.stocks.play_selections import advance

    report["personal_updates"] = advance(market, research, now)
    if board:
        report["first_seen"] = first_seen(research, board, now)
    marked = get_state(research, "marks").get("at")
    trading = market_open(now)
    if folded or not trading or not marked or now - datetime.fromisoformat(marked) >= MARKS_EVERY:
        budget = play_marks.REPAIR_BUDGET_OPEN if trading else play_marks.REPAIR_BUDGET_CLOSED
        report["marks"] = await play_marks.update(market, research, now, history=history, rate=rate, budget=budget)
        put_state(research, "marks", {"at": to_iso(now), **{k: v for k, v in report["marks"].items() if k != "errors"}},
                  now)
    if not trading:
        report["market"] = "closed"
        put_state(research, "idle", report, now)
        return report
    tracked = tracked_set(research, today, board)
    state = get_state(research, "pass")
    archived, newest = archive_ticks(market, tracked, today, state.get("archive_after"), now)
    with transaction(research):
        save_ticks(research, archived)
    last = last_ticks(research, today)
    due_now = due(tracked, last, now)
    endpoint = get_state(research, "endpoint")
    retry = endpoint.get("state") in ("refused", "no_options") and endpoint.get("day") == today.isoformat()

    def save(ticks: list[OptionTick], source: str) -> None:
        with transaction(research):
            save_ticks(research, tick_rows(ticks, clock(), today, source))

    result = await quote_pass(due_now, save, schwab=schwab, chains=chains, use_quotes=not retry)
    report["personal_updates"] += advance(market, research, clock())
    if result.endpoint:
        put_state(research, "endpoint", {"state": result.endpoint, "day": today.isoformat(), "at": to_iso(now)}, now)
    last = last_ticks(research, today)
    report.update({"market": "open", "tracked": len(tracked),
                   "by_priority": dict(Counter(str(t.priority) for t in tracked.values())), "due": len(due_now),
                   "quoted": result.quoted, "from_collector": len(archived), "requests": result.requests,
                   "sources": dict(result.sources or {}), "errors": (result.errors or [])[:5],
                   "missing": len(result.missing or []), "freshness": freshness(tracked, last, clock())})
    history = [h for h in state.get("history", []) if datetime.fromisoformat(h["at"]) > now - timedelta(hours=1)]
    history.append({"at": to_iso(now), "requests": result.requests})
    report["requests_last_hour"] = sum(h["requests"] for h in history)
    put_state(research, "pass", {**report, "archive_after": newest, "history": history}, now)
    return report


def slot(now: datetime) -> str:
    """The five-minute slot a pass belongs to (passes run once per slot)."""
    local = cal.new_york(now)
    return local.replace(minute=local.minute - local.minute % PASS_MINUTES, second=0, microsecond=0).isoformat()


async def cycle(paths: DeskPaths, loaded: LoadedConfig, now: datetime | None = None) -> dict[str, Any] | None:
    """The dashboard's tracking task: one pass per five-minute slot in market hours, one an hour otherwise."""
    from desk.core.secrets import load_secrets
    from desk.stocks.plays import _rate
    from desk.stocks.repo import StocksMarket
    from desk.stocks.runner import saved
    from desk.stocks.suite import build_stocks_suite

    now = now or utc_now()
    market = connect(paths.db_dir / MARKET_FILE, DbKind.STOCKS_MARKET)
    research = connect(paths.db_dir / RESEARCH_FILE, DbKind.STOCKS_RESEARCH)
    runtime = connect(paths.db_dir / "runtime.db", DbKind.RUNTIME)
    suite = None
    try:
        gate = get_state(research, "cycle")
        if market_open(now):
            if gate.get("slot") == slot(now):
                return None
        elif gate.get("at") and now - datetime.fromisoformat(gate["at"]) < timedelta(hours=1) and not research.execute(
                "SELECT 1 FROM stk_track_ticks LIMIT 1").fetchone():
            return None
        try:
            board = saved(paths, "plays")
        except DeskError:
            board = None
        suite = build_stocks_suite(paths, loaded, load_secrets(paths.secrets_file), runtime)
        stocks_market = StocksMarket(market)
        report = await run_pass(market, research, now, board=board, schwab=suite.schwab if suite else None,
                                chains=suite.options if suite else None, history=suite.schwab if suite else None,
                                rate=lambda d: _rate(stocks_market, d))
        put_state(research, "cycle", {"slot": slot(now), "at": to_iso(now)}, now)
        if not market_open(now):
            from desk.stocks import play_learning, play_performance

            learned = play_learning.learn(market, research, now) if now >= cal.session_close(
                cal.last_closed_session(now)) + timedelta(hours=1) else None
            report["learning"] = None if learned is None else {"session": learned["session"],
                                                               "promotions": learned["promotions"]}
            report["performance"] = play_performance.refresh(
                paths, market, research, now,
                changed=bool((report.get("marks") or {}).get("marks")) or learned is not None)
        return report
    finally:
        if suite is not None:
            await suite.aclose()
        market.close()
        research.close()
        runtime.close()


__all__ = ["CLOSE_WINDOW", "INTERVALS", "PASS_MINUTES", "PRIORITY_LABELS", "REQUESTS_PER_PASS", "PassResult",
           "Tracked", "archive_ticks", "compact", "computed_greeks", "cycle", "due", "first_seen", "fold", "fold_due",
           "freshness", "get_state", "last_ticks", "legs_key", "market_open", "pack", "parse_occ", "probe", "put_state",
           "quote_pass", "run_pass", "save_ticks", "slot", "status", "storage", "tick_rows", "tracked_set", "unpack"]
