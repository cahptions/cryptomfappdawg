"""The experience log: every candidate the Learner looks at on a session, not only the picks (docs/STOCKS_LEARNER.md
→ What it records).

**When.** Forward: once, at the first build after a session closes (``record_forward``, called after Plays records
that session), from the same stored chains Plays reads, priced by the same ``plays.table`` (the chain's own smile where
it fits, ``chain_odds``). A session the laptop missed is not recorded later (its scores and quotes would no longer be
the ones of that evening). Replay: ``learner.replay`` builds the same rows from the downloaded real past prices.

**What.** For each stock with a priced chain:

* the **action grid** (``actions.grid``: every structure × |delta| × width × expiry bucket the chain can supply fairly,
  identical legs counted once), capped by ``cap_for``: at most ``PER_UNDERLYING`` a stock, and at most ``PER_DAY``
  a session in all (the per-stock cap falls to ``PER_DAY`` ÷ stocks, never below ``MIN_PER_UNDERLYING``). When the
  grid is larger than the cap the kept candidates are a seeded, structure-stratified sample (round-robin over the
  structures, random within each; the seed is the session, stock and action-set version), so the cap removes no kind
  of trade systematically;
* every **Plays pick** recorded for the session (every section, its Expiry filter cohorts too), origin
  ``plays:<section>#<rank>``, and every **PAPER / Learning option position opened that session**, origin
  ``paper:<account>`` / ``learning:<account>``, valued at the same session's quotes; never capped;
* the **exploration slice**: ``EXPLORE_PER_DAY`` grid candidates a session, chosen at random stratified across the
  structures (round-robin over structures; seeded by the session), marked ``explore``. In Phase 1 they trade
  nothing; a later Learner account can trade them as its unbiased sample.

Each row keeps the exact legs (OCC symbol and side), the entry per share at the quotes (bought legs at the ask, sold at
the bid, as Plays), the money at risk with fees, and the features (``features``). Stored zlib-packed, one row per stock
and session, append-only.
"""

from __future__ import annotations

import json
import random
import sqlite3
import time
import zlib
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from desk.core.clock import to_iso
from desk.core.hashing import canonical_json
from desk.stocks.learner import actions as act
from desk.stocks.learner import features as ft
from desk.stocks.repo import day_of
from desk.storage.db import transaction

if TYPE_CHECKING:
    from desk.stocks.plays import Context, Table

PER_UNDERLYING = 60
MIN_PER_UNDERLYING = 12
PER_DAY = 6000
EXPLORE_PER_DAY = 30
ROW_FIELDS = ("key", "origin", "explore", "action", "entry", "max_loss", "max_gain", "expiration", "legs", "x")


def cap_for(underlyings: int) -> int:
    """Grid candidates kept per stock on a session with ``underlyings`` priced chains."""
    if underlyings <= 0:
        return PER_UNDERLYING
    return max(MIN_PER_UNDERLYING, min(PER_UNDERLYING, PER_DAY // underlyings))


def stratified(items: Sequence[Any], strata: Sequence[str], n: int, seed: str) -> list[int]:
    """Indices of up to ``n`` items: round-robin over the strata (in first-seen order), random within each."""
    rng = random.Random(seed)       # noqa: S311 - a reproducible sample, not a secret
    by: dict[str, list[int]] = defaultdict(list)
    for i, s in enumerate(strata):
        by[s].append(i)
    for v in by.values():
        rng.shuffle(v)
    order = list(by)
    out: list[int] = []
    while len(out) < min(n, len(items)):
        for s in order:
            if by[s] and len(out) < n:
                out.append(by[s].pop())
    return out


def legs_key(legs: Iterable[Sequence[Any]]) -> str:
    return ",".join(sorted(f"{occ}:{side}" for occ, side in legs))


@dataclass
class Row:
    key: str                    # the action's key, or "" for a pick not on the grid
    origin: str                 # grid | plays:<section>#<rank> | paper:<account> | learning:<account>
    action: str                 # BUY | SELL
    entry: float                # per share at the decision quotes
    max_loss: float             # per contract, fees included: the money at risk
    max_gain: float | None
    expiration: int             # ordinal
    legs: list[tuple[str, int]]  # (OCC symbol, +1 bought / −1 sold)
    x: dict[str, float | None]  # the contract features
    explore: int = 0

    def packed(self) -> list[Any]:
        return [self.key, self.origin, self.explore, self.action, round(self.entry, 4), round(self.max_loss, 2),
                None if self.max_gain is None else round(self.max_gain, 2), self.expiration,
                [list(leg) for leg in self.legs], [self.x.get(k) for k in ft.CONTRACT_NAMES]]


@dataclass
class Group:
    """One stock's candidates on one session."""
    symbol: str
    price: float
    decided_at: str
    price_source: str
    stock: dict[str, float | None]
    market: dict[str, float | None]
    rows: list[Row] = field(default_factory=list)

    def blob(self) -> bytes:
        value = {"v": 1, "features": ft.version_hash(), "actions": act.version_hash(), "price": self.price,
                 "stock_names": list(ft.STOCK_NAMES), "stock": [self.stock.get(k) for k in ft.STOCK_NAMES],
                 "market_names": list(ft.MARKET_NAMES), "market": [self.market.get(k) for k in ft.MARKET_NAMES],
                 "fields": list(ROW_FIELDS), "contract_names": list(ft.CONTRACT_NAMES),
                 "rows": [r.packed() for r in self.rows]}
        return zlib.compress(canonical_json(value).encode(), 9)


def unpack(blob: bytes) -> dict[str, Any]:
    """A stored group: {price, stock: {name: value}, market: {...}, rows: [{field: value, x: {name: value}}]}."""
    value = json.loads(zlib.decompress(blob))
    rows = []
    for r in value["rows"]:
        item = dict(zip(value["fields"], r, strict=True))
        item["x"] = dict(zip(value["contract_names"], item["x"], strict=True))
        rows.append(item)
    return {"price": value["price"], "features": value["features"], "actions": value["actions"],
            "stock": dict(zip(value["stock_names"], value["stock"], strict=True)),
            "market": dict(zip(value["market_names"], value["market"], strict=True)), "rows": rows}


# ── composing candidates on a table ──────────────────────────────────────────────────────────────
def priced(tb: Table, structure: str, legs: list[tuple[int, str]], ctx: Context, key: str, origin: str,
           action: act.Action | None) -> Row | None:
    """``plays.finish`` on the legs (the same arithmetic and chances as a play), as a stored row."""
    from desk.stocks import plays as pl

    try:
        play = pl.finish(pl.Candidate("learner", tb, structure, legs, 0.0, []), ctx)
    except (ValueError, KeyError, ZeroDivisionError, IndexError, FloatingPointError):
        return None
    if not play["max_loss"] or play["max_loss"] <= 0 or play["entry"] <= 0:
        return None
    x = ft.contract_features(tb, legs, play, action)
    return Row(key, origin, play["action"], float(play["entry"]), float(play["max_loss"]),
               None if play.get("max_gain") is None else float(play["max_gain"]),
               day_of(date.fromisoformat(play["expiration"])),
               [(tb.quotes[i].occ, 1 if side == "buy" else -1) for i, side in legs], x)


def grid_rows(tb: Table, ctx: Context, cap: int, seed: str) -> tuple[list[Row], int]:
    """The capped grid on one stock: (rows, grid candidates the chain could supply before the cap)."""
    found: list[tuple[act.Action, list[tuple[int, str]]]] = []
    seen: set[str] = set()
    for a in act.grid():
        legs = act.legs_for(tb, a)
        if legs is None:
            continue
        k = legs_key((tb.quotes[i].occ, side) for i, side in legs)
        if k in seen:
            continue
        seen.add(k)
        found.append((a, legs))
    keep = stratified(found, [a.structure for a, _ in found], cap, seed)
    rows = []
    for i in sorted(keep):
        a, legs = found[i]
        row = priced(tb, a.structure, legs, ctx, a.key, "grid", a)
        if row is not None:
            rows.append(row)
    return rows, len(found)


LEG_ORDER = {"BUY": "buy", "SELL": "sell"}


def ordered(structure: str, legs: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Legs in the order ``plays.finish`` reads them (the anchor first; a condor's put side then its call side)."""
    from desk.stocks.plays import TYPES

    if structure in ("iron_condor", "iron_butterfly"):
        puts = sorted((x for x in legs if x["kind"] == "put"), key=lambda x: -float(x["strike"]))
        calls = sorted((x for x in legs if x["kind"] == "call"), key=lambda x: float(x["strike"]))
        return [*puts, *calls]
    first = LEG_ORDER[TYPES[structure][0]]
    return sorted(legs, key=lambda x: (x["side"] != first, x["kind"] != "call"))


def pick_row(tb: Table, ctx: Context, legs: Sequence[Mapping[str, Any]], origin: str) -> Row | None:
    """A Plays pick or a book's position, valued at this session's quotes of its own contracts."""
    from desk.stocks.plays import TYPES, structure_of

    try:
        structure = structure_of(legs)
    except (ValueError, KeyError, IndexError):
        return None
    if structure not in TYPES:
        return None
    index = {q.occ: i for i, q in enumerate(tb.quotes)}
    chosen = []
    for leg in ordered(structure, legs):
        if leg["occ"] not in index:
            return None
        chosen.append((index[leg["occ"]], str(leg["side"])))
    return priced(tb, structure, chosen, ctx, "", origin, None)


def plays_picks(research: sqlite3.Connection, session: date) -> dict[str, list[tuple[str, list[dict[str, Any]]]]]:
    """Each stock's recorded Plays picks of the session: (origin, legs)."""
    out: dict[str, list[tuple[str, list[dict[str, Any]]]]] = defaultdict(list)
    for r in research.execute("SELECT category, rank, symbol, legs_json FROM stk_plays WHERE session=? "
                              "ORDER BY category, rank", (day_of(session),)):
        out[r[2]].append((f"plays:{r[0]}#{r[1]}", json.loads(r[3])))
    return out


def book_entries(books: Sequence[tuple[str, sqlite3.Connection]], session: date
                 ) -> dict[str, list[tuple[str, list[dict[str, Any]]]]]:
    """Each stock's option positions opened on the session in the PAPER and Learning books: (origin, legs)."""
    out: dict[str, list[tuple[str, list[dict[str, Any]]]]] = defaultdict(list)
    for name, conn in books:
        try:
            rows = conn.execute("SELECT account, symbol, legs_json FROM stk_positions WHERE asset='option' AND "
                                "entry_day=?", (day_of(session),)).fetchall()
        except sqlite3.Error:
            continue
        for account, symbol, legs_json in rows:
            legs = []
            for leg in json.loads(legs_json):
                qty = int(leg.get("quantity") or 0)
                if not qty or "occ" not in leg:
                    continue
                legs.append({"occ": leg["occ"], "kind": leg["kind"], "strike": float(leg["strike"]),
                             "expiration": leg["expiration"], "side": "buy" if qty > 0 else "sell"})
            if legs:
                out[symbol].append((f"{name}:{account}", legs))
    return out


# ── recording a session ──────────────────────────────────────────────────────────────────────────
def build_groups(tables: Sequence[Table], ctx: Context, reader_for: Any, market_x: dict[str, float | None],
                 picks: Mapping[str, Sequence[tuple[str, list[dict[str, Any]]]]], *, seed: str,
                 decided_at: Any, explore: bool) -> tuple[list[Group], int]:
    """Every stock's group on one session. Returns (groups, grid candidates supplied before the caps)."""
    cap = cap_for(len(tables))
    groups, supplied = [], 0
    for tb in sorted(tables, key=lambda t: t.u.symbol):
        u = tb.u
        rows, n = grid_rows(tb, ctx, cap, f"{seed}:{u.symbol}")
        supplied += n
        seen = {legs_key(r.legs) for r in rows}
        for origin, legs in picks.get(u.symbol, ()):
            row = pick_row(tb, ctx, legs, origin)
            if row is None:
                continue
            k = legs_key(row.legs)
            if k in seen:              # a pick that is also on the grid: one row, the pick's origin added
                for r in rows:
                    if legs_key(r.legs) == k:
                        r.origin = r.origin if r.origin != "grid" else f"grid+{origin}"
                continue
            seen.add(k)
            rows.append(row)
        stock = ft.stock_features(reader_for(u), u, ctx.rate)
        groups.append(Group(u.symbol, u.price, decided_at(u), u.source, stock, market_x, rows))
    if explore:
        grid = [(g, r) for g in groups for r in g.rows if r.key]
        for i in stratified(grid, [r.key.split("|")[0] for _, r in grid], EXPLORE_PER_DAY, f"{seed}:explore"):
            grid[i][1].explore = 1
    return groups, supplied


def save(conn: sqlite3.Connection, source: str, session: date, groups: Sequence[Group], now: datetime,
         seconds: float) -> dict[str, Any]:
    """Append the session's groups and its day row (once; a second call changes nothing)."""
    av = act.version_hash()
    rows, total = [], 0
    for g in groups:
        if not g.rows:
            continue
        blob = g.blob()
        total += len(blob)
        expirations = ",".join(str(e) for e in sorted({r.expiration for r in g.rows}))
        rows.append((source, day_of(session), g.symbol, av, ft.version_hash(), g.decided_at, g.price_source,
                     len(g.rows), sum(r.explore for r in g.rows), sum(r.origin != "grid" for r in g.rows), expirations,
                     blob, to_iso(now)))
    candidates = sum(r[7] for r in rows)
    explore = sum(r[8] for r in rows)
    with transaction(conn):
        cur = conn.execute("INSERT OR IGNORE INTO stk_learn_days(source, session, actions_version, underlyings, candidates,"
                           " explore, bytes, seconds, recorded_at) VALUES (?,?,?,?,?,?,?,?,?)",
                           (source, day_of(session), av, len(rows), candidates, explore, total, round(seconds, 2),
                            to_iso(now)))
        if cur.rowcount:
            conn.executemany("INSERT OR IGNORE INTO stk_learn_candidates(source, session, symbol, actions_version, "
                             "features_version, decided_at, price_source, candidates, explore, picks, expirations, "
                             "rows_zlib, recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return {"session": session.isoformat(), "underlyings": len(rows), "candidates": candidates, "explore": explore,
            "bytes": total, "recorded": bool(cur.rowcount)}


def recorded(conn: sqlite3.Connection, source: str, session: date) -> bool:
    return conn.execute("SELECT 1 FROM stk_learn_days WHERE source=? AND session=? AND actions_version=?",
                        (source, day_of(session), act.version_hash())).fetchone() is not None


def record_forward(market: Any, research: Any, session: date, now: datetime, *,
                   books: Sequence[tuple[str, sqlite3.Connection]] = ()) -> dict[str, Any]:
    """Record ``session``'s candidates from its stored chains (``market``/``research``: StocksMarket/StocksResearch).
    Called once Plays has recorded that session; idempotent."""
    from desk.stocks import plays as pl

    if recorded(research.conn, "forward", session):
        return {"session": session.isoformat(), "recorded": False, "reason": "already recorded"}
    started = time.monotonic()
    ic, months, ic_source = pl.committee_ic(research)
    ctx = pl.Context(session, now, pl._rate(market, session), ic, months, ic_source, {}, [])
    tables = [tb for u in pl.load(market, research, session) if (tb := pl.table(u, ctx)) is not None]
    reader = ft.Reader(market.conn, research.conn, session)
    spy = next((tb.u for tb in tables if tb.u.symbol == "SPY"), None)
    market_x = ft.market_features(reader, spy, ctx.rate)
    picks: dict[str, list[tuple[str, list[dict[str, Any]]]]] = defaultdict(list)
    for source in (plays_picks(research.conn, session), book_entries(books, session)):
        for symbol, items in source.items():
            picks[symbol].extend(items)
    groups, supplied = build_groups(tables, ctx, lambda u: reader, market_x, picks,
                                    seed=f"forward:{session.isoformat()}:{act.version_hash()}",
                                    decided_at=lambda u: u.observed_at or to_iso(now), explore=True)
    out = save(research.conn, "forward", session, groups, now, time.monotonic() - started)
    return {**out, "supplied": supplied, "seconds": round(time.monotonic() - started, 2)}


def groups_for(conn: sqlite3.Connection, source: str | None = None, *, since: int | None = None,
               until: int | None = None) -> Iterable[sqlite3.Row]:
    """Stored groups (current action set), oldest first."""
    sql = "SELECT * FROM stk_learn_candidates WHERE actions_version=?"
    args: list[Any] = [act.version_hash()]
    if source:
        sql += " AND source=?"
        args.append(source)
    if since is not None:
        sql += " AND session>=?"
        args.append(since)
    if until is not None:
        sql += " AND session<=?"
        args.append(until)
    yield from conn.execute(sql + " ORDER BY session, symbol", args)


def volume(conn: sqlite3.Connection) -> dict[str, Any]:
    """Rows and bytes recorded, per source: sessions, candidates, explore, bytes, per-session averages."""
    out: dict[str, Any] = {}
    for r in conn.execute("SELECT source, COUNT(*), SUM(candidates), SUM(explore), SUM(bytes), MIN(session), "
                          "MAX(session), SUM(underlyings) FROM stk_learn_days GROUP BY source"):
        n = int(r[1])
        out[r[0]] = {"sessions": n, "candidates": int(r[2] or 0), "explore": int(r[3] or 0), "bytes": int(r[4] or 0),
                     "underlyings": int(r[7] or 0),
                     "first": date.fromordinal(r[5]).isoformat() if r[5] else None,
                     "last": date.fromordinal(r[6]).isoformat() if r[6] else None,
                     "candidates_per_session": round((r[2] or 0) / n, 1) if n else 0,
                     "bytes_per_session": round((r[4] or 0) / n) if n else 0}
    return out


__all__ = ["EXPLORE_PER_DAY", "MIN_PER_UNDERLYING", "PER_DAY", "PER_UNDERLYING", "Group", "Row", "book_entries",
           "build_groups", "cap_for", "grid_rows", "groups_for", "legs_key", "ordered", "pick_row", "plays_picks",
           "priced", "record_forward", "recorded", "save", "stratified", "unpack", "volume"]
