"""Plays history: one official mark per play per session, and gap repair (docs/STOCKS_PLAYS.md → History).

**A mark** values the position as held at a session's close. *Liquidation value* is what closing it would bring:
long legs at the bid, short legs bought back at the ask. *Mid value* uses the mids. Profit and loss is per contract
(×100) after fees — opening fees always, closing fees unless the play settled at expiration — and the percentage is
on the money at risk (the debit paid, or a credit spread's worst case). The play's own recorded entry is the basis;
the first-sighting basis is computed beside it on the page.

Each mark carries a status: **quoted** (every leg quoted near the close), **modelled** (tracking ran that session but a
leg had no closing quote: Black-Scholes on the stock's close at that leg's last implied volatility), **estimated** (a
gap — the laptop was off — filled with Black-Scholes on the stock's real closes, the volatility interpolated between
the last quote before the gap and the first one after it) or **settled** (expiration: intrinsic value on the stock's
close). Marks are append-only: a later quote can never change an earlier mark. Grading and learning use quoted marks
and settlements only.

**Gap repair.** Stock closes come from the stored daily bars, else from Schwab's daily price history (requested with
the smallest period that covers the gap), else from the underlying price Schwab reported with the closing option
quotes. Holidays are not sessions and are never filled. A gap with no later quote yet is shown as *provisional*
(volatility held at the last quote) and replaced by permanent estimates once a quote or the expiry brackets it.
For gap sessions Schwab still keeps minute bars for (about five), the intraday path is estimated the same way.
Estimates inside a gap that contains a results filing, a results window or a stock move beyond four standard
deviations are flagged as unreliable.

**Quarantine.** A split between the pick and a session ends the record the day before it (the contract was adjusted;
its old symbol no longer prices the same position). A stock with no price for ten sessions is quarantined as halted or
delisted. Short legs deep in the money with almost no time value carry an early-assignment warning.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from desk.core.clock import to_iso
from desk.core.errors import DeskError
from desk.stocks import calendar as cal
from desk.stocks import options as opt
from desk.stocks.contracts import Bar, IntradayBar
from desk.stocks.paper.fills import OPTION_FEE_PER_CONTRACT as FEE
from desk.stocks.play_tracking import FOLD_AFTER, computed_greeks, pack, parse_occ, put_state
from desk.stocks.repo import day_of
from desk.storage.db import transaction

VERSION = 1
REPAIR_BUDGET_OPEN = 5             # history requests a pass while the market is open (live quotes come first)
REPAIR_BUDGET_CLOSED = 20
HALT_SESSIONS = 10                 # no price for this many sessions: quarantined as halted or delisted
MINUTE_HISTORY_SESSIONS = 5        # Schwab keeps one-minute bars for about five sessions
JUMP_SIGMAS = 4.0
ASSIGNMENT_TIME_VALUE = 0.05
STATUS_ORDER = {"quoted": 0, "modelled": 1, "estimated": 2}


class History(Protocol):
    @property
    def configured(self) -> bool: ...
    async def daily_since(self, symbol: str, since: date, today: date) -> list[Bar]: ...
    async def intraday_history(self, symbol: str) -> list[IntradayBar]: ...


# ── valuing a position ───────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Position:
    session: date
    category: str
    rank: int
    twin: int
    symbol: str
    action: str                 # BUY | SELL
    structure: str
    expiration: date
    legs: list[dict[str, Any]]
    entry: float                # per share: debit paid (BUY) or credit received (SELL)
    max_loss: float             # per contract, fees included
    underlying: float | None = None
    earnings: date | None = None
    identified_at: datetime | None = None

    @property
    def key(self) -> tuple[int, str, int, int]:
        return day_of(self.session), self.category, self.rank, self.twin

    @property
    def settles(self) -> date:
        return settle_day(self.expiration)


def settle_day(expiration: date) -> date:
    """The session whose close settles a contract (the one before, when an expiry falls on a market holiday)."""
    return expiration if cal.is_trading_day(expiration) else cal.previous_trading_day(expiration)


@dataclass(frozen=True)
class LegQuote:
    bid: float
    ask: float
    status: str                 # quoted | modelled | estimated
    underlying: float | None = None


def liquidation(legs: Sequence[dict[str, Any]], quotes: dict[str, LegQuote]) -> tuple[float, float]:
    """Per share, as held (long +, short −): (closing now — long legs at the bid, short legs at the ask; at the mids)."""
    value = mid = 0.0
    for leg in legs:
        q = quotes[leg["occ"]]
        if leg["side"] == "buy":
            value += q.bid
            mid += (q.bid + q.ask) / 2
        else:
            value -= q.ask
            mid -= (q.bid + q.ask) / 2
    return value, mid


def settlement(legs: Sequence[dict[str, Any]], close: float) -> float:
    """Per share at expiration, as held (long +, short −): intrinsic value on the stock's close."""
    total = 0.0
    for leg in legs:
        intrinsic = max(0.0, close - leg["strike"]) if leg["kind"] == "call" else max(0.0, leg["strike"] - close)
        total += intrinsic if leg["side"] == "buy" else -intrinsic
    return total


def profit(action: str, entry: float, max_loss: float, value: float, legs: int, *, settled: bool) -> tuple[float, float]:
    """(P/L per contract after fees, P/L as a fraction of the money at risk)."""
    fees = FEE * legs * (1 if settled else 2)
    if action == "BUY":
        pnl = (value - entry) * 100 - fees
        return pnl, pnl / (entry * 100 + FEE * legs)
    pnl = (entry + value) * 100 - fees
    return pnl, pnl / max_loss if max_loss > 0 else 0.0


def assignment_risk(position: Position, close: float | None, quotes: dict[str, LegQuote]) -> str | None:
    if close is None:
        return None
    for leg in position.legs:
        if leg["side"] != "sell":
            continue
        intrinsic = max(0.0, close - leg["strike"]) if leg["kind"] == "call" else max(0.0, leg["strike"] - close)
        q = quotes.get(leg["occ"])
        if intrinsic > 0 and q is not None and (q.bid + q.ask) / 2 - intrinsic < ASSIGNMENT_TIME_VALUE:
            return (f"Early-assignment risk: the short {leg['kind']} at {leg['strike']:g} is in the money with under "
                    f"${ASSIGNMENT_TIME_VALUE:.2f} of time value left")
    return None


# ── what is recorded ─────────────────────────────────────────────────────────────────────────────
def positions(research: sqlite3.Connection, since: date | None = None) -> list[Position]:
    """Every recorded play and its twin, oldest first."""
    out = []
    floor = day_of(since) if since else 0
    for r in research.execute("SELECT session, category, rank, symbol, action, structure, expiration, legs_json, entry,"
                              " max_loss, underlying, json_extract(play_json, '$.earnings') AS earnings FROM stk_plays"
                              " WHERE session >= ? ORDER BY session, category, rank", (floor,)):
        out.append(Position(date.fromordinal(r["session"]), r["category"], r["rank"], 0, r["symbol"], r["action"],
                            r["structure"], date.fromordinal(r["expiration"]), json.loads(r["legs_json"]), r["entry"],
                            r["max_loss"], r["underlying"], date.fromisoformat(r["earnings"]) if r["earnings"] else None))
    for r in research.execute("SELECT * FROM stk_play_twins WHERE session >= ? ORDER BY session, category, rank",
                              (floor,)):
        legs = json.loads(r["legs_json"])
        out.append(Position(date.fromordinal(r["session"]), r["category"], r["rank"], 1, r["symbol"], r["action"],
                            r["structure"], date.fromordinal(r["expiration"]), legs, r["entry"], r["max_loss"],
                            legs[0].get("underlying") if legs else None))
    from desk.stocks.play_archive import ARCHIVE_CATEGORY

    for row in research.execute("SELECT * FROM stk_play_ideas WHERE session>=?", (floor,)):
        play = json.loads(row["play_json"])
        out.append(Position(date.fromordinal(row["session"]), ARCHIVE_CATEGORY+row["id"], 1, 0, play["symbol"],
                            play["action"],play["structure"],date.fromisoformat(play["expiration"]),play["legs"],
                            play["entry"],play["max_loss"],play.get("underlying_price"),
                            identified_at=datetime.fromisoformat(row["identified_at"])))
    return out


def split_day(market: sqlite3.Connection, symbol: str, after: date, through: date) -> date | None:
    row = market.execute("SELECT MIN(execution_date) FROM stk_splits WHERE symbol = ? AND execution_date > ? AND "
                         "execution_date <= ?", (symbol, day_of(after), day_of(through))).fetchone()
    return date.fromordinal(row[0]) if row and row[0] else None


def _chunks(items: Sequence[Any], n: int = 400) -> Iterable[Sequence[Any]]:
    for i in range(0, len(items), n):
        yield items[i:i + n]


class Closes:
    """Stock closes by (symbol, session): official bars, then Schwab history, then the quote-time underlying."""

    def __init__(self, market: sqlite3.Connection, research: sqlite3.Connection, symbols: Iterable[str], start: date,
                 end: date) -> None:
        self.market, self.research = market, research
        self.symbols = sorted(set(symbols))
        self.start, self.end = start, end
        self.official: dict[tuple[str, int], float] = {}
        self.quoted: dict[tuple[str, int], float] = {}
        self.load()

    def load(self) -> None:
        lo, hi = day_of(self.start), day_of(self.end)
        for chunk in _chunks(self.symbols):
            marks = ",".join("?" * len(chunk))
            for r in self.market.execute(f"SELECT symbol, day, close FROM stk_bars WHERE symbol IN ({marks}) "  # noqa: S608
                                         "AND day BETWEEN ? AND ?", (*chunk, lo, hi)):
                self.official[(r["symbol"], r["day"])] = float(r["close"])
            for r in self.research.execute(f"SELECT symbol, session, close FROM stk_track_closes WHERE symbol IN "  # noqa: S608
                                           f"({marks}) AND session BETWEEN ? AND ?", (*chunk, lo, hi)):
                self.official.setdefault((r["symbol"], r["session"]), float(r["close"]))
        for r in self.research.execute("SELECT occ, session, underlying FROM stk_track_days WHERE session BETWEEN ? AND ?"
                                       " AND status = 'quoted' AND underlying > 0 AND source != 'recorded play'",
                                       (lo, hi)):
            self.quoted[(r["occ"][:-15], r["session"])] = float(r["underlying"])

    def official_close(self, symbol: str, day: date) -> float | None:
        return self.official.get((symbol, day_of(day)))

    def close(self, symbol: str, day: date) -> float | None:
        key = (symbol, day_of(day))
        return self.official.get(key, self.quoted.get(key))


# ── repair: stock closes from Schwab history ─────────────────────────────────────────────────────
async def fetch_closes(market: sqlite3.Connection, research: sqlite3.Connection, history: History,
                       wanted: dict[str, date], today: date, now: datetime,
                       budget: int) -> tuple[int, list[str], list[str]]:
    """Fetch daily candles for symbols whose closes are missing back to ``wanted[symbol]``. Candles are kept only
    when they agree with stored official closes (Schwab's history may be split-adjusted) and only after the
    symbol's last known split. Returns (requests, errors, symbols answered): a refused or failed request is not
    an answer, so that symbol is asked again on the next pass rather than hours later."""
    requests, errors, answered = 0, [], []
    for symbol, since in sorted(wanted.items(), key=lambda kv: kv[1]):
        if requests >= budget:
            break
        requests += 1
        try:
            bars = await history.daily_since(symbol, since, today)
        except DeskError as exc:
            errors.append(f"{symbol} history: {exc}"[:200])
            continue
        answered.append(symbol)
        split = market.execute("SELECT MAX(execution_date) FROM stk_splits WHERE symbol = ?", (symbol,)).fetchone()[0]
        last_closed = cal.last_closed_session(now, FOLD_AFTER)
        rows = [(symbol, day_of(b.day), b.close, "schwab_history", to_iso(now)) for b in bars
                if since <= b.day <= last_closed and cal.is_trading_day(b.day) and (split is None or day_of(b.day) >= split)]
        official = {r["day"]: r["close"] for r in market.execute(
            "SELECT day, close FROM stk_bars WHERE symbol = ? AND day >= ?", (symbol, day_of(since) - 10))}
        overlap = [(official[r[1]], r[2]) for r in rows if r[1] in official]
        if any(abs(c / o - 1) > 0.01 for o, c in overlap):
            errors.append(f"{symbol}: Schwab's history disagrees with the stored closes (adjusted?); not used")
            continue
        with transaction(research):
            research.executemany("INSERT OR IGNORE INTO stk_track_closes(symbol, session, close, source, fetched_at)"
                                 " VALUES (?, ?, ?, ?, ?)", rows)
    return requests, errors, answered


# ── repair: contract rows for sessions without a closing quote ───────────────────────────────────
@dataclass
class DayRow:
    status: str
    bid: float
    ask: float
    underlying: float | None
    iv: float | None
    quoted: bool                # a real quote (live or recorded), usable as an interpolation anchor
    note: str | None = None


def load_rows(research: sqlite3.Connection, occs: Sequence[str], since: date) -> dict[str, dict[int, DayRow]]:
    out: dict[str, dict[int, DayRow]] = defaultdict(dict)
    for chunk in _chunks(list(occs)):
        marks = ",".join("?" * len(chunk))
        for r in research.execute(f"SELECT occ, session, status, bid, ask, underlying, iv, schwab_iv, note "  # noqa: S608
                                  f"FROM stk_track_days WHERE occ IN ({marks}) AND session >= ?", (*chunk, day_of(since))):
            if r["bid"] is None or r["ask"] is None:
                continue
            out[r["occ"]][r["session"]] = DayRow(r["status"], r["bid"], r["ask"], r["underlying"],
                                                 r["iv"] or r["schwab_iv"], r["status"] == "quoted", r["note"])
    return out


def tracked_sessions(research: sqlite3.Connection, since: date) -> set[int]:
    """Sessions in which live tracking captured closing quotes (so a contract missing from them is modelled)."""
    return {r[0] for r in research.execute("SELECT DISTINCT session FROM stk_track_days WHERE session >= ? AND "
                                           "status = 'quoted' AND source != 'recorded play'", (day_of(since),))}


def gap_flags(market: sqlite3.Connection, closes: Closes, position: Position, start: date, end: date) -> list[str]:
    """Why an estimate across ``start``..``end`` may be unreliable."""
    flags = []
    if position.earnings and start - timedelta(days=1) <= position.earnings <= end + timedelta(days=1):
        flags.append(f"results window ({position.earnings.isoformat()}) inside the gap")
    row = market.execute("SELECT f.filed FROM stk_filings f JOIN stk_listings l ON l.cik = f.cik WHERE l.symbol = ? AND "
                         "f.form LIKE '8-K%' AND f.items LIKE '%2.02%' AND f.filed BETWEEN ? AND ? LIMIT 1",
                         (position.symbol, day_of(start) - 1, day_of(end))).fetchone()
    if row is not None:
        flags.append(f"results filed {date.fromordinal(row[0]).isoformat()}")
    sessions = cal.trading_days(start - timedelta(days=45), end)
    prices = [(d, closes.close(position.symbol, d)) for d in sessions]
    series = [(d, c) for d, c in prices if c]
    rets = [(series[i][0], math.log(series[i][1] / series[i - 1][1])) for i in range(1, len(series))]
    before = [r for d, r in rets if d < start]
    if len(before) >= 10:
        sd = math.sqrt(sum(r * r for r in before) / len(before))
        big = [d for d, r in rets if d >= start and sd > 0 and abs(r) > JUMP_SIGMAS * sd]
        if big:
            flags.append(f"stock moved more than {JUMP_SIGMAS:g} standard deviations on {big[0].isoformat()}")
    return flags


def model_price(kind: str, s: float, strike: float, expiration: date, day: date, iv: float, rate: float) -> float:
    t = max((expiration - day).days, 0.25) / opt.YEAR_DAYS
    return opt.bs_price(kind, s, strike, t, iv, rate)


@dataclass
class Repair:
    rows: list[tuple[Any, ...]] = field(default_factory=list)            # new stk_track_days rows
    estimated: list[tuple[str, date, float]] = field(default_factory=list)   # (occ, day, iv) for intraday estimates
    provisional: dict[tuple[str, int], DayRow] = field(default_factory=dict)


def recorded_quote(occ: str, record_day: date, leg: dict[str, Any], underlying: float | None,
                   rows: dict[int, DayRow], rate: Callable[[date], float], now: datetime, out: Repair) -> None:
    """The play's own closing quote (from the chain it was picked from) is a real quote for its first session."""
    n = day_of(record_day)
    if n in rows or leg.get("bid") is None or leg.get("ask") is None:
        return
    _, kind, strike, expiration = parse_occ(occ)
    iv = computed_greeks(kind, (float(leg["bid"]) + float(leg["ask"])) / 2, underlying, strike, expiration, record_day,
                         rate(record_day))["iv"]
    row = DayRow("quoted", float(leg["bid"]), float(leg["ask"]), underlying, iv or leg.get("iv"), True,
                 "the play's recorded closing quote")
    rows[n] = row
    out.rows.append(_row(occ, record_day, row, "recorded play", rate, now))


def fill_contract(occ: str, days: Sequence[date], rows: dict[int, DayRow], closes: Closes, ran: set[int],
                  last_closed: date, rate: Callable[[date], float], now: datetime, out: Repair) -> None:
    """Create the missing daily rows of one contract, oldest first (each may anchor the next)."""
    symbol, kind, strike, expiration = parse_occ(occ)
    settles = settle_day(expiration)
    for d in days:
        n = day_of(d)
        if n in rows:
            continue
        s = closes.close(symbol, d)
        if s is None:
            continue                                   # no stock price yet (or a halt): nothing to fill
        if d == settles:
            s = closes.official_close(symbol, d)
            if s is None:
                continue                               # settlement waits for the official close
            intrinsic = max(0.0, s - strike) if kind == "call" else max(0.0, strike - s)
            row = DayRow("settled", intrinsic, intrinsic, s, None, False, "expiration: intrinsic value on the close")
            rows[n] = row
            out.rows.append(_row(occ, d, row, "settlement", rate, now))
            continue
        before = [(k, r) for k, r in rows.items() if k < n and r.iv]
        if not before:
            continue
        k0, r0 = max(before, key=lambda kr: kr[0])
        anchors = [(k, r) for k, r in rows.items() if k < n and r.quoted and r.iv]
        a0 = max(anchors, key=lambda kr: kr[0]) if anchors else (k0, r0)
        if n in ran:
            iv = float(r0.iv or 0)
            price = model_price(kind, s, strike, expiration, d, iv, rate(d))
            row = DayRow("modelled", price, price, s, iv, False, "no closing quote: Black-Scholes at the last implied "
                         "volatility on the stock's close")
            rows[n] = row
            out.rows.append(_row(occ, d, row, "model", rate, now))
            continue
        after = [(k, r) for k, r in rows.items() if k > n and r.quoted and r.iv]
        if after:
            k1, r1 = min(after, key=lambda kr: kr[0])
            span = cal.trading_days(date.fromordinal(a0[0]), date.fromordinal(k1))
            pos = sum(1 for x in span if day_of(x) <= n) - 1
            w = pos / max(len(span) - 1, 1)
            iv = float(a0[1].iv or 0) * (1 - w) + float(r1.iv or 0) * w
            note = (f"estimated: the laptop was off; implied volatility interpolated between "
                    f"{date.fromordinal(a0[0]).isoformat()} and {date.fromordinal(k1).isoformat()}")
        elif settles <= last_closed or any(k > n for k in ran):
            # expired, or tracking has run since without quoting this contract (those sessions are modelled at the
            # same held volatility): nothing will bracket this gap any more
            iv = float(a0[1].iv or 0)
            note = ("estimated: the laptop was off and no later quote of this contract exists; implied volatility "
                    "held at the last quote")
        else:
            iv = float(a0[1].iv or 0)
            price = model_price(kind, s, strike, expiration, d, iv, rate(d))
            out.provisional[(occ, n)] = DayRow("estimated", price, price, s, iv, False,
                                               "provisional estimate: implied volatility held at the last quote until "
                                               "the next quote brackets the gap")
            continue
        price = model_price(kind, s, strike, expiration, d, iv, rate(d))
        row = DayRow("estimated", price, price, s, iv, False, note)
        rows[n] = row
        out.rows.append(_row(occ, d, row, "repair", rate, now))
        out.estimated.append((occ, d, iv))


def _row(occ: str, d: date, row: DayRow, source: str, rate: Callable[[date], float], now: datetime) -> tuple[Any, ...]:
    _, kind, strike, expiration = parse_occ(occ)
    mid = (row.bid + row.ask) / 2
    g = computed_greeks(kind, mid, row.underlying, strike, expiration, d, rate(d)) if row.status == "quoted" else {
        "iv": row.iv, "delta": None, "gamma": None, "theta": None, "vega": None}
    return (occ, day_of(d), row.status, row.bid, row.ask, mid, None, None, None, row.underlying, g["iv"] or row.iv,
            g["delta"], g["gamma"], g["theta"], g["vega"], None, None, None, None, None, None, None, None, source,
            row.note, to_iso(now))


def save_rows(research: sqlite3.Connection, rows: list[tuple[Any, ...]]) -> None:
    research.executemany(
        "INSERT OR IGNORE INTO stk_track_days(occ, session, status, bid, ask, mark, last, volume, open_interest,"
        " underlying, iv, delta, gamma, theta, vega, schwab_iv, schwab_delta, schwab_gamma, schwab_theta,"
        " schwab_vega, high_mid, low_mid, quoted_at, source, note, recorded_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)


async def estimate_intraday(research: sqlite3.Connection, history: History, estimated: list[tuple[str, date, float]],
                            last_closed: date, rate: Callable[[date], float], now: datetime,
                            budget: int) -> tuple[int, list[str]]:
    """Estimated five-minute paths for recent gap sessions, from Schwab's minute bars of the stock."""
    recent = set(cal.trading_days(last_closed - timedelta(days=10), last_closed)[-MINUTE_HISTORY_SESSIONS:])
    by_symbol: dict[str, list[tuple[str, date, float]]] = defaultdict(list)
    for occ, d, iv in estimated:
        if d in recent and not research.execute("SELECT 1 FROM stk_track_intraday WHERE occ = ? AND session = ?",
                                                (occ, day_of(d))).fetchone():
            by_symbol[occ[:-15]].append((occ, d, iv))
    requests, errors, rows = 0, [], []
    for symbol, items in sorted(by_symbol.items()):
        if requests >= budget:
            break
        requests += 1
        try:
            bars = await history.intraday_history(symbol)
        except DeskError as exc:
            errors.append(f"{symbol} minute bars: {exc}"[:200])
            continue
        for occ, d, iv in items:
            _, kind, strike, expiration = parse_occ(occ)
            expiry_close = cal.session_close(expiration)
            points: list[list[float | None]] = []
            for b in bars:
                local = cal.new_york(b.at)
                minute = local.hour * 60 + local.minute + 1      # the bar's close
                if local.date() != d or minute % 5 or not 9 * 60 + 35 <= minute <= 16 * 60:
                    continue
                t = max((expiry_close - b.at).total_seconds() / (opt.YEAR_DAYS * 86400), 1e-4)
                price = round(opt.bs_price(kind, b.close, strike, t, iv, rate(d)), 4)
                points.append([minute, price, price, b.close, None])
            if points:
                rows.append((occ, day_of(d), "estimated", len(points), pack(points), to_iso(now)))
    if rows:
        with transaction(research):
            research.executemany("INSERT OR IGNORE INTO stk_track_intraday(occ, session, status, points, packed,"
                                 " folded_at) VALUES (?, ?, ?, ?, ?, ?)", rows)
    return requests, errors


# ── marks ────────────────────────────────────────────────────────────────────────────────────────
def mark_day(position: Position, d: date, rows: dict[str, dict[int, DayRow]], closes: Closes,
             provisional: dict[tuple[str, int], DayRow] | None = None) -> dict[str, Any] | None:
    """The mark of one position on one session, or None while a leg (or the settlement close) is missing."""
    n = day_of(d)
    legs = position.legs
    if d == position.settles:
        close = closes.official_close(position.symbol, d)
        if close is None:
            return None                                   # settlement waits for the official close
        value = settlement(legs, close)
        pnl, pct = profit(position.action, position.entry, position.max_loss, value, len(legs), settled=True)
        return {"status": "settled", "value": value, "mid": value, "pnl": pnl, "pnl_pct": pct, "underlying": close,
                "note": "settled at expiration on the stock's close"}
    quotes: dict[str, LegQuote] = {}
    notes: list[str] = []
    provisional_used = False
    for leg in legs:
        row = rows.get(leg["occ"], {}).get(n)
        if row is None and provisional is not None:
            row = provisional.get((leg["occ"], n))
            provisional_used = provisional_used or row is not None
        if row is None or row.status == "settled":
            return None
        quotes[leg["occ"]] = LegQuote(row.bid, row.ask, row.status, row.underlying)
        if row.note and row.status != "quoted" and row.note not in notes:
            notes.append(row.note)
    status = max((q.status for q in quotes.values()), key=STATUS_ORDER.__getitem__)
    value, mid = liquidation(legs, quotes)
    pnl, pct = profit(position.action, position.entry, position.max_loss, value, len(legs), settled=False)
    underlying = next((q.underlying for q in quotes.values() if q.underlying), None) or closes.close(position.symbol, d)
    risk = assignment_risk(position, underlying, quotes)
    if risk:
        notes.append(risk)
    return {"status": status, "value": value, "mid": mid, "pnl": pnl, "pnl_pct": pct, "underlying": underlying,
            "note": "; ".join(notes) or None, "provisional": provisional_used}


def _quarantine(research: sqlite3.Connection, position: Position, reason: str, now: datetime) -> None:
    research.execute("INSERT OR IGNORE INTO stk_play_quarantine(session, category, rank, twin, reason, at) "
                     "VALUES (?, ?, ?, ?, ?, ?)", (*position.key, reason, to_iso(now)))


REFETCH_AFTER = timedelta(hours=6)   # a symbol's history is not requested again sooner


def _open_positions(market: sqlite3.Connection, research: sqlite3.Connection, last_closed: date,
                    now: datetime) -> tuple[list[tuple[Position, list[date]]], int]:
    """Positions with sessions still to mark, and those sessions. Completed positions cost one grouped query."""
    counts = {(r[0], r[1], r[2], r[3]): r[4] for r in research.execute(
        "SELECT session, category, rank, twin, COUNT(*) FROM stk_play_marks GROUP BY session, category, rank, twin")}
    out, quarantined = [], 0
    for p in positions(research):
        beginning = max(p.session,cal.new_york(p.identified_at).date()) if p.identified_at else p.session
        if beginning > last_closed:
            continue
        target = min(p.settles, last_closed)
        split = split_day(market, p.symbol, p.session, target)
        if split is not None:
            _quarantine(research, p, f"Split on {split.isoformat()}: the contract was adjusted, so its record ends the "
                                     "session before", now)
            quarantined += 1
            target = cal.previous_trading_day(split)
        sessions = cal.trading_days(beginning, target)
        if counts.get(p.key, 0) >= len(sessions):
            continue
        done = {r[0] for r in research.execute("SELECT day FROM stk_play_marks WHERE session = ? AND category = ? AND "
                                               "rank = ? AND twin = ?", p.key)}
        missing = [d for d in sessions if day_of(d) not in done]
        if missing:
            out.append((p, missing))
    return out, quarantined


async def update(market: sqlite3.Connection, research: sqlite3.Connection, now: datetime, *,
                 history: History | None, rate: Callable[[date], float], budget: int) -> dict[str, Any]:
    """Repair gaps and write every mark that has become possible. Safe to run any number of times."""
    from desk.stocks.play_tracking import get_state

    last_closed = cal.last_closed_session(now, FOLD_AFTER)
    today = cal.new_york(now).date()
    with transaction(research):
        open_positions, quarantined = _open_positions(market, research, last_closed, now)
    report: dict[str, Any] = {"open": len(open_positions), "requests": 0, "errors": [], "quarantined": quarantined}
    if not open_positions:
        with transaction(research):
            research.execute("DELETE FROM stk_play_marks_provisional")
            put_state(research, "repair", {"pending": 0, "provisional": 0, "at": to_iso(now)}, now)
        report.update({"marks": 0, "provisional": 0, "pending": 0, "contract_rows": 0})
        return report
    start = min(p.session for p, _ in open_positions)    # from the picks: earlier quotes anchor later estimates
    closes = Closes(market, research, {p.symbol for p, _ in open_positions}, start - timedelta(days=60), last_closed)
    occs = sorted({leg["occ"] for p, _ in open_positions for leg in p.legs})
    rows = load_rows(research, occs, start)
    repair = Repair()
    for p, _ in open_positions:                    # real quotes first: they anchor every estimate
        if p.identified_at is not None:
            continue  # an intraday first-sighting quote is never relabelled as that day's closing quote
        for leg in p.legs:
            recorded_quote(leg["occ"], p.session, leg, p.underlying, rows[leg["occ"]], rate, now, repair)
    # stock closes still missing where a contract has no row, or for a settlement: Schwab history, within the budget
    need: dict[str, date] = {}
    for p, missing in open_positions:
        for d in missing:
            settling = d == p.settles and closes.official_close(p.symbol, d) is None
            unquoted = any(day_of(d) not in rows[leg["occ"]] for leg in p.legs) and closes.close(p.symbol, d) is None
            if settling or unquoted:
                need[p.symbol] = min(need.get(p.symbol, d), d)
    fetched = get_state(research, "history_fetched")
    need = {s: d for s, d in need.items() if not fetched.get(s) or
            now - datetime.fromisoformat(fetched[s]) >= REFETCH_AFTER}
    if need and history is not None and history.configured:
        with transaction(research):
            put_state(research, "repair", {"pending": len(open_positions), "fetching": len(need),
                                           "note": f"fetching stock history for {len(need)} symbols",
                                           "at": to_iso(now)}, now)
        spent, errors, asked = await fetch_closes(market, research, history, need, today, now, budget)
        report["requests"] += spent
        report["errors"].extend(errors)
        with transaction(research):
            put_state(research, "history_fetched", {**{k: v for k, v in fetched.items()
                                                       if now - datetime.fromisoformat(v) < REFETCH_AFTER},
                                                    **{sym: to_iso(now) for sym in asked}}, now)
        closes.load()
    ran = tracked_sessions(research, start)
    for p, _ in open_positions:
        days = cal.trading_days(p.session, min(p.settles, last_closed))
        for leg in p.legs:
            fill_contract(leg["occ"], days, rows[leg["occ"]], closes, ran, last_closed, rate, now, repair)
    with transaction(research):
        save_rows(research, repair.rows)
    report["contract_rows"] = len(repair.rows)
    if history is not None and history.configured and repair.estimated and report["requests"] < budget:
        spent, errors = await estimate_intraday(research, history, repair.estimated, last_closed, rate, now,
                                                budget - report["requests"])
        report["requests"] += spent
        report["errors"].extend(errors)
    marks, provisional, pending, halted = [], [], 0, []
    for p, missing in open_positions:
        flags: list[str] | None = None
        incomplete = False
        for d in missing:
            m = mark_day(p, d, rows, closes)
            if m is None:
                pm = mark_day(p, d, rows, closes, repair.provisional)
                if pm is not None:
                    provisional.append((*p.key, day_of(d), round(pm["value"], 4), round(pm["mid"], 4),
                                        round(pm["pnl"], 2), round(pm["pnl_pct"], 5), pm["underlying"], pm["note"],
                                        to_iso(now)))
                else:
                    incomplete = True
                continue
            if m["status"] == "estimated":
                if flags is None:
                    gap = [x for x in missing if any(
                        (r := rows[leg["occ"]].get(day_of(x))) is not None and r.status == "estimated" for leg in p.legs)]
                    flags = gap_flags(market, closes, p, min(gap or [d]), max(gap or [d]))
                if flags:
                    m["note"] = "; ".join([*([m["note"]] if m["note"] else []), "unreliable: " + ", ".join(flags)])
            marks.append((*p.key, day_of(d), m["status"], round(m["value"], 4), round(m["mid"], 4), round(m["pnl"], 2),
                          round(m["pnl_pct"], 5), m["underlying"], m["note"], to_iso(now)))
        if incomplete:
            pending += 1
            stale = [d for d in missing if closes.close(p.symbol, d) is None]
            if len(stale) >= HALT_SESSIONS and stale[-1] == missing[-1]:
                halted.append(p)
    with transaction(research):
        for p in halted:
            _quarantine(research, p, "No stock price for ten sessions: halted or delisted", now)
        research.executemany("INSERT OR IGNORE INTO stk_play_marks(session, category, rank, twin, day, status, value,"
                             " mid, pnl, pnl_pct, underlying, note, recorded_at) VALUES"
                             " (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", marks)
        research.execute("DELETE FROM stk_play_marks_provisional")
        research.executemany("INSERT OR IGNORE INTO stk_play_marks_provisional(session, category, rank, twin, day,"
                             " value, mid, pnl, pnl_pct, underlying, note, computed_at) VALUES"
                             " (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", provisional)
        put_state(research, "repair", {"pending": pending, "provisional": len({x[:4] for x in provisional}),
                                       "requests": report["requests"], "errors": report["errors"][:5],
                                       "note": "waiting for stock closes or a later quote" if pending else None,
                                       "at": to_iso(now)}, now)
    report.update({"marks": len(marks), "provisional": len(provisional), "pending": pending,
                   "quarantined": quarantined + len(halted)})
    return report


__all__ = ["Closes", "DayRow", "LegQuote", "Position", "Repair", "assignment_risk", "estimate_intraday",
           "fetch_closes", "fill_contract", "gap_flags", "liquidation", "load_rows", "mark_day", "model_price",
           "positions", "profit", "recorded_quote", "settle_day", "settlement", "split_day", "tracked_sessions",
           "update"]
