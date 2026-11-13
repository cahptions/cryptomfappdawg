"""The Learner's labels: what happened to every recorded candidate (docs/STOCKS_LEARNER.md → What happened).

**Checkpoints.** ``s1``, ``s5``, ``s10`` and ``s21``: the candidate valued at the closing quote of the session that many
sessions after its decision, as held: *liquidation* (bought legs at the bid, sold legs bought back at the ask) and
*mid*. A candidate that expired by then counts at its settlement. ``exp:<day>``: every candidate expiring that day,
settled at intrinsic value on the official close of its settlement session (``play_marks.settle_day``), with the
**maximum favourable and adverse excursion** over the checked closes before it (and the settlement), and the return of
**every exit rule** in ``actions.EXIT_RULES`` on that same path.

**Returns** use Plays' own arithmetic (``play_marks.profit``): per contract after fees, on the money at risk; a bought
position's loss stops at −100% (it can always be left to expire), a sold one's at its maximum loss.

**Never estimated.** A closing quote is a stored quote of that session observed within ``FRESH_CLOSE`` of its close
(forward: the chains collected that session; replay: the contract's own Massive daily bar, its closing trade with the
replay's stated spread). A leg without one makes that checkpoint *missing* for the candidate, permanently; a split
between the decision and a checkpoint makes it missing too (the contract was adjusted). Nothing is modelled.

**When.** Forward checkpoints are written ``LABEL_AFTER`` after their session's close (the evening's collection is
done); a settlement waits for the official close up to ``GRADE_WINDOW`` days, then is written missing. Replay
checkpoints are written once their session's stock bars are stored. Every write is ``INSERT OR IGNORE`` keyed by
candidate group and checkpoint, so the job is idempotent and append-only: a label, once written, never changes.
"""

from __future__ import annotations

import json
import sqlite3
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from desk.core.clock import to_iso
from desk.core.hashing import canonical_json, sha256_hex
from desk.stocks import calendar as cal
from desk.stocks.learner import actions as act
from desk.stocks.learner.experience import unpack
from desk.stocks.play_marks import profit, settle_day, settlement
from desk.stocks.repo import _unpack, day_of
from desk.storage.db import transaction

NAME = "stocks_learner_labels"
VERSION = 1
CHECKPOINTS = (1, 5, 10, 21)
FRESH_CLOSE = timedelta(minutes=60)     # a forward closing quote: observed no earlier than this before the close
LABEL_AFTER = timedelta(hours=3)        # forward checkpoints are written this long after their session's close
GRADE_WINDOW = 45                       # days a settlement waits for the official close before it is written missing
BUDGET = 400                            # candidate groups labelled per call (the next call continues)
STATUS = ("quoted", "settled", "missing")


def content() -> dict[str, Any]:
    return {"version": VERSION, "checkpoints": list(CHECKPOINTS), "fresh_close_minutes": FRESH_CLOSE.seconds // 60,
            "label_after_hours": LABEL_AFTER.seconds // 3600, "grade_window_days": GRADE_WINDOW,
            "exit_rules": [[r.id, r.take_profit, r.stop, r.sessions] for r in act.EXIT_RULES],
            "returns": "play_marks.profit: per contract after fees on the money at risk; floors at -100%"}


def version_hash() -> str:
    return sha256_hex(canonical_json(content()))[:16]


class Prices(Protocol):
    """Where later prices come from: the forward chains or the replay's daily option bars."""
    source: str

    def quotes(self, symbol: str, day: date, occs: Sequence[str], expiration: int | None = None
               ) -> dict[str, tuple[float, float]]: ...

    def path(self, symbol: str, occs: Sequence[str], expiration: int, first: date, last: date
             ) -> dict[int, dict[str, tuple[float, float]]]: ...

    def close(self, symbol: str, day: date) -> float | None: ...

    def ready(self, day: date, now: datetime) -> bool: ...


def _good(bid: Any, ask: Any) -> bool:
    return bid is not None and ask is not None and 0 <= float(bid) <= float(ask) and float(ask) > 0


class ForwardPrices:
    """The chains collected on each session (``stk_option_chains``): a quote counts as that session's closing quote
    when it was observed no earlier than ``FRESH_CLOSE`` before the session's close."""
    source = "forward"

    def __init__(self, market: sqlite3.Connection, research: sqlite3.Connection | None = None) -> None:
        self.market, self.research = market, research

    def _rows(self, sql: str, args: tuple[Any, ...], occs: Sequence[str]) -> dict[int, dict[str, tuple[float, float]]]:
        wanted = set(occs)
        out: dict[int, dict[str, tuple[float, float]]] = {}
        for day, observed, blob in self.market.execute(sql, args):
            d = date.fromordinal(day)
            fresh = cal.session_close(d) - FRESH_CLOSE
            got = out.setdefault(int(day), {})
            for q in _unpack(blob):
                if q[0] not in wanted:
                    continue
                at = datetime.fromisoformat(q[9] if q[9] else observed)
                if at >= fresh and _good(q[3], q[4]):
                    got[q[0]] = (float(q[3]), float(q[4]))
        return out

    def quotes(self, symbol: str, day: date, occs: Sequence[str], expiration: int | None = None
               ) -> dict[str, tuple[float, float]]:
        return self._rows("SELECT day, observed_at, chain_zlib FROM stk_option_chains WHERE underlying=? AND day=?",
                          (symbol, day_of(day)), occs).get(day_of(day), {})

    def path(self, symbol: str, occs: Sequence[str], expiration: int, first: date, last: date
             ) -> dict[int, dict[str, tuple[float, float]]]:
        return self._rows("SELECT day, observed_at, chain_zlib FROM stk_option_chains WHERE underlying=? AND "
                          "expiration=? AND day>=? AND day<=?", (symbol, expiration, day_of(first), day_of(last)), occs)

    def close(self, symbol: str, day: date) -> float | None:
        row = self.market.execute("SELECT close FROM stk_bars WHERE symbol=? AND day=?", (symbol, day_of(day))).fetchone()
        if (row is None or not row[0]) and self.research is not None:
            try:
                row = self.research.execute("SELECT close FROM stk_track_closes WHERE symbol=? AND session=?",
                                            (symbol, day_of(day))).fetchone()
            except sqlite3.OperationalError:
                row = None
        return float(row[0]) if row and row[0] else None

    def ready(self, day: date, now: datetime) -> bool:
        return now >= cal.session_close(day) + LABEL_AFTER


class ReplayPrices:
    """Massive's daily option bars: the contract's closing trade that session with the replay's stated spread
    (``play_replay.HALF_SPREAD``, at least ``MIN_HALF``); no trade that session, no quote."""
    source = "replay"

    def __init__(self, market: sqlite3.Connection) -> None:
        self.market = market
        latest = market.execute("SELECT MAX(day) FROM stk_bar_days WHERE rows > 0").fetchone()[0]
        self.latest = int(latest or 0)

    @staticmethod
    def _quote(close: float) -> tuple[float, float]:
        from desk.stocks.play_replay import HALF_SPREAD, MIN_HALF

        half = max(MIN_HALF, HALF_SPREAD * close)
        return round(max(close - half, 0.01), 4), round(close + half, 4)

    def _bars(self, occs: Sequence[str], first: int, last: int) -> dict[int, dict[str, tuple[float, float]]]:
        out: dict[int, dict[str, tuple[float, float]]] = {}
        if not occs:
            return out
        marks = ",".join("?" * len(occs))       # placeholders only; values are bound
        for occ, day, close, volume in self.market.execute(
                f"SELECT occ, day, close, volume FROM stk_option_bars WHERE day>=? AND day<=? AND occ IN ({marks})",  # noqa: S608
                (first, last, *occs)):
            if close and close > 0 and (volume or 0) > 0:
                out.setdefault(int(day), {})[occ] = self._quote(float(close))
        return out

    def quotes(self, symbol: str, day: date, occs: Sequence[str], expiration: int | None = None
               ) -> dict[str, tuple[float, float]]:
        return self._bars(occs, day_of(day), day_of(day)).get(day_of(day), {})

    def path(self, symbol: str, occs: Sequence[str], expiration: int, first: date, last: date
             ) -> dict[int, dict[str, tuple[float, float]]]:
        return self._bars(occs, day_of(first), day_of(last))

    def close(self, symbol: str, day: date) -> float | None:
        row = self.market.execute("SELECT close FROM stk_bars WHERE symbol=? AND day=?", (symbol, day_of(day))).fetchone()
        return float(row[0]) if row and row[0] else None

    def ready(self, day: date, now: datetime) -> bool:
        return day_of(day) <= self.latest


# ── valuing one candidate ────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Candidate:
    index: int
    action: str
    entry: float
    max_loss: float
    expiration: int
    legs: list[dict[str, Any]]      # occ, kind, strike, side

    @classmethod
    def of(cls, index: int, row: Mapping[str, Any]) -> Candidate:
        from desk.stocks.play_tracking import parse_occ

        legs = []
        for occ, sign in row["legs"]:
            _, kind, strike, _ = parse_occ(occ)
            legs.append({"occ": occ, "kind": kind, "strike": strike, "side": "buy" if sign > 0 else "sell"})
        return cls(index, row["action"], float(row["entry"]), float(row["max_loss"]), int(row["expiration"]), legs)

    def liquidation(self, quotes: Mapping[str, tuple[float, float]]) -> tuple[float, float] | None:
        """(return at liquidation value, return at mid) at these quotes; None when a leg has none."""
        value = mid = 0.0
        for leg in self.legs:
            q = quotes.get(leg["occ"])
            if q is None:
                return None
            bid, ask = q
            if leg["side"] == "buy":
                value += bid
                mid += (bid + ask) / 2
            else:
                value -= ask
                mid -= (bid + ask) / 2
        n = len(self.legs)
        return (profit(self.action, self.entry, self.max_loss, value, n, settled=False)[1],
                profit(self.action, self.entry, self.max_loss, mid, n, settled=False)[1])

    def settled(self, close: float) -> float:
        return profit(self.action, self.entry, self.max_loss, settlement(self.legs, close), len(self.legs),
                      settled=True)[1]


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 5)


def exit_returns(path: Sequence[float | None], settled: float | None) -> list[float | None]:
    """Each exit rule's return on a path of liquidation returns (``path[n-1]`` is the n-th session after the
    decision; None where no closing quote was stored) and the settlement return (None when unknown)."""
    out: list[float | None] = []
    for rule in act.EXIT_RULES:
        result, decided = None, False
        for n, r in enumerate(path, 1):
            if rule.sessions is not None and n == rule.sessions:
                result, decided = r, True
                break
            if r is None:
                continue
            if (rule.take_profit is not None and r >= rule.take_profit) or (rule.stop is not None and r <= rule.stop):
                result, decided = r, True
                break
        out.append(_r(result if decided else settled))
    return out


# ── the job ──────────────────────────────────────────────────────────────────────────────────────
def _nth_session(session: date, n: int) -> date:
    d = session
    for _ in range(n):
        d = cal.next_trading_day(d)
    return d


def _split(market: sqlite3.Connection, symbol: str, after: date, through: date) -> bool:
    return market.execute("SELECT 1 FROM stk_splits WHERE symbol=? AND execution_date>? AND execution_date<=?",
                          (symbol, day_of(after), day_of(through))).fetchone() is not None


def _pack(rows: list[list[Any]]) -> bytes:
    return zlib.compress(canonical_json({"v": 1, "rows": rows}).encode(), 9)


def unpack_labels(blob: bytes) -> list[list[Any]]:
    return list(json.loads(zlib.decompress(blob))["rows"])


def label_group(market: sqlite3.Connection, prices: Prices, g: sqlite3.Row, have: set[str], now: datetime
                ) -> list[tuple[str, int, list[list[Any]], int]]:
    """The checkpoints of one candidate group that are due and not yet written: (checkpoint, day, rows, have)."""
    session = date.fromordinal(g["session"])
    symbol = g["symbol"]
    cands = [Candidate.of(i, r) for i, r in enumerate(unpack(g["rows_zlib"])["rows"])]
    occs = sorted({leg["occ"] for c in cands for leg in c.legs})
    out: list[tuple[str, int, list[list[Any]], int]] = []
    settles: dict[int, tuple[date, float | None, bool]] = {}     # expiration -> (settle day, close, final)

    def settle(expiration: int) -> tuple[date, float | None, bool]:
        if expiration not in settles:
            d = settle_day(date.fromordinal(expiration))
            close = None if _split(market, symbol, session, d) else prices.close(symbol, d)
            final = close is not None or (prices.ready(d, now) and (now.date() - d).days > GRADE_WINDOW) or (
                _split(market, symbol, session, d))
            settles[expiration] = (d, close, final)
        return settles[expiration]

    for n in CHECKPOINTS:
        name = f"s{n}"
        if name in have:
            continue
        day = _nth_session(session, n)
        if not prices.ready(day, now):
            continue
        expired = [c for c in cands if settle(c.expiration)[0] <= day]
        if any(not settle(c.expiration)[2] for c in expired):
            continue                                    # a settlement close still due
        split = _split(market, symbol, session, day)
        quotes = {} if split else prices.quotes(symbol, day, occs)
        rows, count = [], 0
        for c in cands:
            if split:
                rows.append([c.index, "missing", None, None])
                continue
            d, close, _ = settle(c.expiration)
            if d <= day:
                ret = None if close is None else c.settled(close)
                rows.append([c.index, "settled" if ret is not None else "missing", _r(ret), _r(ret)])
            else:
                got = c.liquidation(quotes)
                rows.append([c.index, "quoted", _r(got[1]), _r(got[0])] if got else [c.index, "missing", None, None])
            count += rows[-1][1] != "missing"
        out.append((name, day_of(day), rows, count))
    for expiration in sorted({c.expiration for c in cands}):
        name = f"exp:{expiration}"
        if name in have:
            continue
        d, close, final = settle(expiration)
        if not final or not prices.ready(d, now):
            continue
        group = [c for c in cands if c.expiration == expiration]
        mine = sorted({leg["occ"] for c in group for leg in c.legs})
        last = cal.previous_trading_day(d)
        path = {} if last <= session else prices.path(symbol, mine, expiration, cal.next_trading_day(session), last)
        days = [] if last <= session else cal.trading_days(cal.next_trading_day(session), last)
        split_on = next((x for x in days if _split(market, symbol, session, x)), None)
        rows, count = [], 0
        for c in group:
            settled = None if close is None else c.settled(close)
            series: list[float | None] = []
            for x in days:
                got = None if split_on is not None and x >= split_on else c.liquidation(path.get(day_of(x), {}))
                series.append(None if got is None else got[0])
            checked = [r for r in series if r is not None] + ([settled] if settled is not None else [])
            rows.append([c.index, _r(settled), _r(max(checked)) if checked else None, _r(min(checked)) if checked else None,
                         len(series), sum(r is not None for r in series), exit_returns(series, settled)])
            count += settled is not None
        out.append((name, day_of(d), rows, count))
    return out


def pending(conn: sqlite3.Connection, source: str, limit: int = BUDGET) -> list[sqlite3.Row]:
    """Candidate groups with checkpoints not yet written under the current label rules, oldest first."""
    lv = version_hash()
    return conn.execute(
        "SELECT c.*, (SELECT group_concat(l.checkpoint) FROM stk_learn_labels l WHERE l.source=c.source AND "
        "l.session=c.session AND l.symbol=c.symbol AND l.actions_version=c.actions_version AND l.labels_version=?) "
        "AS done FROM stk_learn_candidates c WHERE c.source=? AND c.actions_version=? AND "
        "(SELECT COUNT(*) FROM stk_learn_labels l WHERE l.source=c.source AND l.session=c.session AND "
        "l.symbol=c.symbol AND l.actions_version=c.actions_version AND l.labels_version=?) < "
        "? + length(c.expirations) - length(replace(c.expirations, ',', '')) + 1 "
        "ORDER BY c.session, c.symbol LIMIT ?", (lv, source, act.version_hash(), lv, len(CHECKPOINTS), limit)).fetchall()


def run(conn: sqlite3.Connection, market: sqlite3.Connection, prices: Prices, now: datetime, *,
        limit: int = BUDGET) -> dict[str, Any]:
    """Write every due checkpoint of up to ``limit`` pending groups; idempotent."""
    lv = version_hash()
    written = groups = 0
    for g in pending(conn, prices.source, limit):
        have = set((g["done"] or "").split(",")) - {""}
        found = label_group(market, prices, g, have, now)
        if not found:
            continue
        groups += 1
        with transaction(conn):
            for name, day, rows, count in found:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO stk_learn_labels(source, session, symbol, actions_version, labels_version, "
                    "checkpoint, day, rows, have, labels_zlib, labelled_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (g["source"], g["session"], g["symbol"], g["actions_version"], lv, name, day, len(rows), count,
                     _pack(rows), to_iso(now)))
                written += cur.rowcount
    return {"source": prices.source, "groups": groups, "checkpoints": written}


def coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    """Labels written per source and checkpoint: groups, candidate rows and how many had a value."""
    out: dict[str, Any] = {}
    for source, checkpoint, n, rows, have in conn.execute(
            "SELECT source, CASE WHEN checkpoint LIKE 'exp:%' THEN 'expiry' ELSE checkpoint END k, COUNT(*), SUM(rows), "
            "SUM(have) FROM stk_learn_labels WHERE labels_version=? GROUP BY source, k", (version_hash(),)):
        out.setdefault(source, {})[checkpoint] = {"groups": int(n), "rows": int(rows or 0), "have": int(have or 0)}
    return out


__all__ = ["CHECKPOINTS", "FRESH_CLOSE", "GRADE_WINDOW", "LABEL_AFTER", "NAME", "VERSION", "Candidate",
           "ForwardPrices", "Prices", "ReplayPrices", "content", "coverage", "exit_returns", "label_group", "pending",
           "run", "unpack_labels", "version_hash"]
