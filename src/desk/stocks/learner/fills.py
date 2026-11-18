"""The fill model: what a limit order between the mid and the far side can expect (docs/STOCKS_LEARNER.md → Costs:
the expected fill). An ordinary binned estimate with priors; no language model, nothing modelled beyond the counts.

**The question.** An order placed against a saved quote (bid, ask) with its limit a fraction ``f`` of the way from the
mid to the far side (``f`` in ``FRACTIONS``: 0 = the mid, 1 = the far side, paying the ask or taking the bid): how
likely is it to fill within the session, and at what price? A limit at ``f`` fills at ``mid ± f × half spread``; at
``f = 1`` the order is marketable and always fills at the far side.

**Observed fills only.** From KESTREL's saved quote history (``stk_track_intraday``: the Schwab quotes of every tracked
contract, every five to sixty minutes through the session): for each contract and session the first saved quote is the
anchor, and a limit at ``f`` counts as **filled** only when a *later* saved quote of the same session crossed it (a
buy: an ask at or below the limit; a sell: a bid at or above it). Nothing else counts: a contract with no later quote
gives no observation. PAPER and Learning fills are not used: they are marketable orders at the far side, so they say
nothing about a limit inside the spread.

**Estimated by cells, shrunk, monotone.** Each observation falls in a cell: the anchor's spread (as a share of the mid),
the previous session's open interest and volume (known before the order), days to expiry and moneyness
(``*_EDGES``; an unknown open interest or volume counts as the lowest bin). For each ``f < 1`` the fill rate of a cell
is shrunk toward its spread bin's, which is shrunk toward all observations', which is shrunk toward **no fill**, each by
``PRIOR`` pseudo-observations; then the rates are made non-decreasing in ``f`` (isotonic, pool-adjacent-violators). With
no observations every limit inside the spread has fill chance 0: the far-side price (pay the ask).

**No look-ahead.** ``Fills.at(through)`` uses observations of sessions up to ``through`` only: a decision on session S
is costed with what was known by S's close.

**A structure** fills when every leg would: its chance is the product of its legs' (conservative: the legs' fills are
positively related in reality), and the price saved is ``(1 − f)`` × the sum of its legs' half spreads. The chosen
fraction ``best`` maximises ``p(f) × (EDGE + saving(f) / money at risk)``: a limit is tried only when the expected
saving is worth the chance of missing a trade assumed to earn ``EDGE`` at the far side; ties go to the higher ``f``.
"""

from __future__ import annotations

import json
import math
import sqlite3
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import numpy as np

from desk.core.clock import to_iso
from desk.core.hashing import canonical_json, sha256_hex
from desk.stocks import calendar as cal
from desk.stocks.learner import actions as act
from desk.stocks.paper.fills import OPTION_FEE_PER_CONTRACT as FEE
from desk.storage.db import transaction

NAME = "stocks_learner_fills"
VERSION = 1
FRACTIONS = (0.0, 0.25, 0.5, 0.75, 1.0)
INSIDE = FRACTIONS[:-1]                 # the limits that can miss; f = 1 always fills
SPREAD_EDGES = (0.02, 0.05, 0.10, 0.20)
OI_EDGES = (100, 1000)
VOLUME_EDGES = (10, 100)
DTE_EDGES = (14, 60)
MONEYNESS_EDGES = (0.03, 0.10)
PRIOR = 20.0
EDGE = 0.10
SESSIONS = 20                           # sessions of quotes turned into observations per call
GROUPS = 400                            # candidate groups whose decision legs are recorded per call
MATCH = 0.011                           # recomputed entry must match the recorded one within a cent a share
SHAPE = (len(SPREAD_EDGES) + 1, len(OI_EDGES) + 1, len(VOLUME_EDGES) + 1, len(DTE_EDGES) + 1, len(MONEYNESS_EDGES) + 1)
CELLS = int(np.prod(SHAPE))


def content() -> dict[str, Any]:
    return {"version": VERSION, "fractions": list(FRACTIONS), "spread_edges": list(SPREAD_EDGES),
            "oi_edges": list(OI_EDGES), "volume_edges": list(VOLUME_EDGES), "dte_edges": list(DTE_EDGES),
            "moneyness_edges": list(MONEYNESS_EDGES), "prior": PRIOR, "edge": EDGE, "match": MATCH,
            "observed": "anchor = first saved quote of the session; filled only when a later saved quote that session "
                        "crossed the limit (buy: ask <= limit; sell: bid >= limit); buy and sell pooled",
            "estimate": "cell -> spread bin -> all -> no fill, each shrunk by `prior` pseudo-observations; isotonic in f",
            "structure": "product of the legs' chances; saving (1 - f) x sum of half spreads; best f maximises "
                         "p(f) x (edge + saving / money at risk), ties to the higher f; no data -> the far side",
            "source": "stk_track_intraday (Schwab quotes of tracked contracts)"}


def version_hash() -> str:
    return sha256_hex(canonical_json(content()))[:16]


# ── observations ─────────────────────────────────────────────────────────────────────────────────
def _bin(x: float | None, edges: Sequence[float]) -> int:
    return 0 if x is None or not math.isfinite(x) else int(np.searchsorted(np.asarray(edges, float), x, side="right"))


def cell(spread: float | None, oi: float | None, volume: float | None, dte: float | None,
         moneyness: float | None) -> tuple[int, int]:
    """(cell index, spread bin) of a quote's facts (an unknown open interest or volume counts as the lowest bin)."""
    bins = (_bin(spread, SPREAD_EDGES), _bin(oi, OI_EDGES), _bin(volume, VOLUME_EDGES), _bin(dte, DTE_EDGES),
            _bin(None if moneyness is None else abs(moneyness), MONEYNESS_EDGES))
    return int(np.ravel_multi_index(bins, SHAPE)), bins[0]


def observe(points: Sequence[Sequence[Any]]) -> tuple[float, list[int], list[int]] | None:
    """From one contract's saved quotes of one session (``[minute, bid, ask, ...]`` in time order): the anchor's
    spread share and, for each ``f`` in ``INSIDE``, whether a buy and a sell limit at ``f`` were crossed by a *later*
    quote. None when the anchor is unusable (no spread, no mid) or no later quote was saved."""
    usable = [p for p in points if p[1] is not None and p[2] is not None and 0 <= p[1] <= p[2] and p[2] > 0]
    if len(usable) < 2:
        return None
    _, bid, ask = usable[0][:3]
    mid, half = (bid + ask) / 2, (ask - bid) / 2
    if half <= 0 or mid <= 0:
        return None
    later = usable[1:]
    low_ask = min(float(p[2]) for p in later)
    high_bid = max(float(p[1]) for p in later)
    buy = [int(low_ask <= mid + f * half + 1e-9) for f in INSIDE]
    sell = [int(high_bid >= mid - f * half - 1e-9) for f in INSIDE]
    return 2 * half / mid, buy, sell


def _pack(rows: list[list[Any]]) -> bytes:
    return zlib.compress(json.dumps({"v": 1, "rows": rows}, allow_nan=False, separators=(",", ":")).encode(), 9)


def _unpack(blob: bytes) -> list[list[Any]]:
    return list(json.loads(zlib.decompress(blob))["rows"])


def session_observations(research: sqlite3.Connection, session: date) -> list[list[Any]]:
    """Every tracked contract's observation for ``session``: [occ, spread, oi, volume, dte, moneyness, buy, sell]."""
    from desk.stocks.play_tracking import parse_occ, unpack

    prev = cal.previous_trading_day(session).toordinal()
    out = []
    for occ, blob in research.execute("SELECT occ, packed FROM stk_track_intraday WHERE session=? AND status='quoted' "
                                      "ORDER BY occ", (session.toordinal(),)):
        points = unpack(blob)
        got = observe(points)
        if got is None:
            continue
        try:
            _, _, strike, expiration = parse_occ(occ)
        except ValueError:
            continue
        spread, buy, sell = got
        before = research.execute("SELECT open_interest, volume FROM stk_track_days WHERE occ=? AND session=?",
                                  (occ, prev)).fetchone()
        underlying = points[0][3] if len(points[0]) > 3 else None
        out.append([occ, round(spread, 5), None if before is None else before[0], None if before is None else before[1],
                    (expiration - session).days, None if not underlying else round(strike / float(underlying) - 1, 5),
                    buy, sell])
    return out


def collect(research: sqlite3.Connection, now: datetime, *, limit: int = SESSIONS) -> dict[str, Any]:
    """Turn every folded session of saved quotes not yet observed into fill observations (idempotent)."""
    v = version_hash()
    try:
        todo = [int(r[0]) for r in research.execute(
            "SELECT DISTINCT session FROM stk_track_intraday WHERE session NOT IN (SELECT session FROM "
            "stk_learn_fill_obs WHERE fills_version=?) ORDER BY session LIMIT ?", (v, limit))]
    except sqlite3.OperationalError:
        return {"sessions": 0, "reason": "no saved quote history in this database"}
    written = rows = 0
    for s in todo:
        session = date.fromordinal(s)
        if now < cal.session_close(session):
            continue
        obs = session_observations(research, session)
        with transaction(research):
            research.execute("INSERT OR IGNORE INTO stk_learn_fill_obs(session, fills_version, rows, obs_zlib, "
                             "recorded_at) VALUES (?,?,?,?,?)", (s, v, len(obs), _pack(obs), to_iso(now)))
        written += 1
        rows += len(obs)
    return {"sessions": written, "observations": rows}


# ── the estimate ─────────────────────────────────────────────────────────────────────────────────
def _pav(values: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Non-decreasing least-squares fit (pool adjacent violators) of a short sequence."""
    blocks: list[tuple[float, float, int]] = [(float(v), float(w), 1) for v, w in zip(values, weights, strict=True)]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][0] > blocks[i + 1][0] + 1e-12:
            a, b = blocks[i], blocks[i + 1]
            w = a[1] + b[1]
            blocks[i] = ((a[0] * a[1] + b[0] * b[1]) / w, w, a[2] + b[2])
            del blocks[i + 1]
            i = max(i - 1, 0)
        else:
            i += 1
    return np.asarray([b[0] for b in blocks for _ in range(b[2])])


@dataclass
class Table:
    """Fill chances by cell (``CELLS`` × ``FRACTIONS``; the last column, the far side, is 1) from ``n`` observations."""
    p: np.ndarray
    n: int
    through: int | None

    def chance(self, spread: float | None, oi: float | None, volume: float | None, dte: float | None,
               moneyness: float | None) -> np.ndarray:
        out: np.ndarray = self.p[cell(spread, oi, volume, dte, moneyness)[0]]
        return out


def estimate(counts: np.ndarray) -> np.ndarray:
    """Shrunk, monotone fill chances from counts (``CELLS`` × ``INSIDE`` × [filled, observed])."""
    filled, seen = counts[..., 0], counts[..., 1]
    p_all = filled.sum(axis=0) / (seen.sum(axis=0) + PRIOR)                         # toward no fill
    spread_bin = np.unravel_index(np.arange(CELLS), SHAPE)[0]
    p_spread = np.zeros((SHAPE[0], len(INSIDE)))
    for b in range(SHAPE[0]):
        rows = spread_bin == b
        p_spread[b] = (filled[rows].sum(axis=0) + PRIOR * p_all) / (seen[rows].sum(axis=0) + PRIOR)
    p_cell = (filled + PRIOR * p_spread[spread_bin]) / (seen + PRIOR)
    out = np.ones((CELLS, len(FRACTIONS)))
    for c in range(CELLS):
        out[c, :-1] = np.clip(_pav(p_cell[c], seen[c] + PRIOR), 0.0, 1.0)
    return out


@dataclass
class Fills:
    """Every observation's counts, cumulated by session, so the estimate as of any session is cheap."""
    sessions: np.ndarray                    # sorted sessions with observations
    cumulative: np.ndarray                  # len(sessions) × CELLS × INSIDE × 2
    cache: dict[int, Table]

    @classmethod
    def from_rows(cls, by_session: Mapping[int, Sequence[Sequence[Any]]]) -> Fills:
        sessions = np.asarray(sorted(by_session), dtype=np.int64)
        per = np.zeros((len(sessions), CELLS, len(INSIDE), 2))
        for i, s in enumerate(sessions.tolist()):
            for _occ, spread, oi, volume, dte, mny, buy, sell in by_session[s]:
                c = cell(spread, oi, volume, dte, mny)[0]
                for side in (buy, sell):
                    per[i, c, :, 0] += np.asarray(side, float)
                    per[i, c, :, 1] += 1
        return cls(sessions, np.cumsum(per, axis=0), {})

    @classmethod
    def load(cls, research: sqlite3.Connection) -> Fills:
        rows: dict[int, list[list[Any]]] = {}
        try:
            for s, blob in research.execute("SELECT session, obs_zlib FROM stk_learn_fill_obs WHERE fills_version=?",
                                            (version_hash(),)):
                rows[int(s)] = _unpack(blob)
        except sqlite3.OperationalError:
            pass
        return cls.from_rows(rows)

    def at(self, through: int | None = None) -> Table:
        """The estimate from observations of sessions up to ``through`` (all when None)."""
        i = len(self.sessions) - 1 if through is None else int(np.searchsorted(self.sessions, through, "right")) - 1
        if i not in self.cache:
            counts = self.cumulative[i] if i >= 0 else np.zeros((CELLS, len(INSIDE), 2))
            self.cache[i] = Table(estimate(counts), int(counts[:, 0, 1].sum()) if i >= 0 else 0,
                                  None if i < 0 else int(self.sessions[i]))
        return self.cache[i]

    def observations(self) -> int:
        return int(self.cumulative[-1][:, 0, 1].sum()) if len(self.sessions) else 0


# ── a candidate's expected fill ──────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Fill:
    """A structure's expected fill at each fraction, and the one chosen."""
    p: tuple[float, ...]                    # chance of filling within the session, per FRACTIONS
    saving: float                           # per share at the mid (f = 0): the sum of half spreads
    best: int                               # index into FRACTIONS

    @property
    def fraction(self) -> float:
        return FRACTIONS[self.best]

    @property
    def chance(self) -> float:
        return self.p[self.best]

    def saved(self, i: int | None = None) -> float:
        """Per share saved against the far side at FRACTIONS[i] (the chosen one by default)."""
        return (1 - FRACTIONS[self.best if i is None else i]) * self.saving


FAR_SIDE = Fill((*(0.0 for _ in INSIDE), 1.0), 0.0, len(FRACTIONS) - 1)


def structure_fill(table: Table, legs: Sequence[Sequence[Any]] | None, risk: float) -> Fill:
    """The expected fill of a structure from its legs' decision facts (``[mid, half, oi, volume, dte, moneyness]``)
    and its money at risk per contract; the far side when the legs are unknown."""
    if not legs or risk <= 0:
        return FAR_SIDE
    p = np.ones(len(FRACTIONS))
    saving = 0.0
    for mid, half, oi, volume, dte, mny in legs:
        if mid is None or half is None or mid <= 0:
            return FAR_SIDE
        p *= table.chance(2 * half / mid, oi, volume, dte, mny)
        saving += half
    value = [p[i] * (EDGE + (1 - f) * saving * 100 / risk) for i, f in enumerate(FRACTIONS)]
    best = max(range(len(FRACTIONS)), key=lambda i: (round(value[i], 12), i))
    return Fill(tuple(round(float(x), 6) for x in p), saving, best)


def at_fill(ret: float, action: str, entry: float, max_loss: float, legs: int, saved: float) -> float:
    """A realised return on the money at risk at the far-side entry (``play_marks.profit``) restated for an entry
    ``saved`` per share better: the same exit or settlement value, the same fees, the floor at −100% kept."""
    if saved <= 0:
        return ret
    if action == "BUY":
        cost = entry * 100 + FEE * legs
        proceeds = ret * cost + cost                       # what the exit or settlement brought, after its fees
        better = cost - saved * 100
        return (proceeds - better) / better if better > 0 else ret
    pnl = ret * max_loss + saved * 100
    risk = max_loss - saved * 100
    return max(pnl, -risk) / risk if risk > 0 else ret


def net(ret: float, action: str, entry: float, max_loss: float, legs: int, fill: Fill) -> float:
    """The expected return of trying the trade at the chosen limit: its chance of filling × the return at that fill
    (a missed fill is no trade: zero)."""
    return fill.chance * at_fill(ret, action, entry, max_loss, legs, fill.saved())


# ── decision legs, recorded while the chain is stored ─────────────────────────────────────────────
def _chain(market: sqlite3.Connection, symbol: str, session: int) -> dict[str, tuple[float, float, Any, Any]]:
    from desk.stocks.repo import _unpack as unpack_chain

    out: dict[str, tuple[float, float, Any, Any]] = {}
    for (blob,) in market.execute("SELECT chain_zlib FROM stk_option_chains WHERE underlying=? AND day=?",
                                  (symbol, session)):
        for q in unpack_chain(blob):
            if q[3] is not None and q[4] is not None and 0 <= q[3] <= q[4] and q[4] > 0:
                out[q[0]] = (float(q[3]), float(q[4]), q[7], q[6])
    return out


def decision_legs(row: Mapping[str, Any], chain: Mapping[str, tuple[float, float, Any, Any]], session: date,
                  price: float) -> list[list[Any]] | None:
    """One candidate's legs at its decision quotes, or None when a leg is missing or the recomputed entry differs from
    the recorded one (the stored chain is not the one it was priced from)."""
    from desk.stocks.play_tracking import parse_occ

    legs, debit = [], 0.0
    for occ, sign in row["legs"]:
        q = chain.get(occ)
        if q is None:
            return None
        bid, ask, oi, volume = q
        _, _, strike, expiration = parse_occ(occ)
        debit += ask if sign > 0 else -bid
        legs.append([round((bid + ask) / 2, 4), round((ask - bid) / 2, 4), oi, volume, (expiration - session).days,
                     round(strike / price - 1, 5) if price else None])
    entry = debit if row["action"] == "BUY" else -debit
    return legs if abs(entry - float(row["entry"])) <= MATCH else None


def record_entries(research: sqlite3.Connection, market: sqlite3.Connection, now: datetime, *,
                   limit: int = GROUPS) -> dict[str, Any]:
    """Record the decision legs of up to ``limit`` forward candidate groups that have none yet (idempotent)."""
    from desk.stocks.learner.experience import unpack

    av = act.version_hash()
    todo = research.execute(
        "SELECT c.source, c.session, c.symbol, c.rows_zlib FROM stk_learn_candidates c WHERE c.source='forward' AND "
        "c.actions_version=? AND NOT EXISTS (SELECT 1 FROM stk_learn_entry e WHERE e.source=c.source AND "
        "e.session=c.session AND e.symbol=c.symbol AND e.actions_version=c.actions_version) ORDER BY c.session, "
        "c.symbol LIMIT ?", (av, limit)).fetchall()
    groups = have = 0
    for source, session, symbol, blob in todo:
        g = unpack(blob)
        chain = _chain(market, symbol, int(session))
        day = date.fromordinal(int(session))
        rows = [[i, decision_legs(r, chain, day, float(g["price"]))] for i, r in enumerate(g["rows"])] if chain else []
        found = sum(r[1] is not None for r in rows)
        with transaction(research):
            research.execute("INSERT OR IGNORE INTO stk_learn_entry(source, session, symbol, actions_version, status, "
                             "rows, have, legs_zlib, recorded_at) VALUES (?,?,?,?,?,?,?,?,?)",
                             (source, session, symbol, av, "quoted" if chain else "missing", len(g["rows"]), found,
                              _pack(rows) if chain else None, to_iso(now)))
        groups += 1
        have += found
    return {"groups": groups, "candidates_with_legs": have}


def entry_legs(research: sqlite3.Connection, source: str, session: int, symbol: str) -> dict[int, list[list[Any]]]:
    """A group's recorded decision legs by candidate index (empty when none were recorded)."""
    try:
        row = research.execute("SELECT legs_zlib FROM stk_learn_entry WHERE source=? AND session=? AND symbol=? AND "
                               "actions_version=?", (source, session, symbol, act.version_hash())).fetchone()
    except sqlite3.OperationalError:
        return {}
    if row is None or row[0] is None:
        return {}
    return {int(i): legs for i, legs in _unpack(row[0]) if legs is not None}


def saved_quotes(research: sqlite3.Connection, occ: str, session: date) -> list[tuple[float, float]]:
    """Every saved (bid, ask) of a contract during ``session``: the folded intraday history and any ticks not yet
    folded (the per-contract quotes of tracked contracts). Empty when none were saved."""
    from desk.stocks.play_tracking import unpack

    out: list[tuple[float, float]] = []
    try:
        row = research.execute("SELECT packed FROM stk_track_intraday WHERE occ=? AND session=? AND status='quoted'",
                               (occ, session.toordinal())).fetchone()
        if row is not None:
            out += [(float(p[1]), float(p[2])) for p in unpack(row[0]) if p[1] is not None and p[2] is not None]
        out += [(float(b), float(a)) for b, a in research.execute(
            "SELECT bid, ask FROM stk_track_ticks WHERE occ=? AND session=? AND bid IS NOT NULL AND ask IS NOT NULL",
            (occ, session.toordinal()))]
    except sqlite3.OperationalError:
        return []
    return [(b, a) for b, a in out if 0 <= b <= a and a > 0]


def limit_fill(limits: Mapping[str, tuple[int, float]], snapshot: Mapping[str, tuple[float, float]],
               saved: Mapping[str, Sequence[tuple[float, float]]], chance: float, draw: float) -> tuple[bool, str]:
    """Whether a limit entry fills on its session (``limits``: occ → (+1 bought / −1 sold, limit price)), and how it
    was decided. **Observed** when a stored quote of that session crossed every leg's limit (the session's chain
    snapshot or a saved intraday quote: a buy's ask at or below it, a sell's bid at or above it), or when every leg has
    saved intraday quotes and one never crossed. Otherwise the fill model's ``chance`` against the ``draw`` recorded at
    the decision (filled when ``draw < chance``)."""
    crossed, covered = True, True
    for occ, (sign, limit) in limits.items():
        quotes = [*([snapshot[occ]] if occ in snapshot else []), *saved.get(occ, ())]
        hit = any((a <= limit + 1e-9) if sign > 0 else (b >= limit - 1e-9) for b, a in quotes)
        crossed &= hit
        covered &= bool(saved.get(occ))
    if crossed:
        return True, "observed: a stored quote of the session crossed the limit"
    if covered:
        return False, "observed: no saved quote of the session crossed the limit"
    return draw < chance, (f"fill model: {chance:.0%} chance, seeded draw {draw:.3f} "
                           + ("(filled)" if draw < chance else "(not filled)"))


def summary(research: sqlite3.Connection) -> dict[str, Any]:
    """What the fill model rests on, and its chances for a typical liquid and a typical wide contract (plain words)."""
    fills = Fills.load(research)
    table = fills.at()
    try:
        entries = research.execute("SELECT COUNT(*), SUM(rows), SUM(have) FROM stk_learn_entry").fetchone()
    except sqlite3.OperationalError:
        entries = (0, 0, 0)
    examples = {"liquid": table.chance(0.03, 5000, 500, 30, 0.02), "wide": table.chance(0.15, 50, 5, 30, 0.08)}
    return {"version": version_hash(), "observations": fills.observations(), "sessions": len(fills.sessions),
            "first": date.fromordinal(int(fills.sessions[0])).isoformat() if len(fills.sessions) else None,
            "last": date.fromordinal(int(fills.sessions[-1])).isoformat() if len(fills.sessions) else None,
            "entries": {"groups": int(entries[0] or 0), "candidates": int(entries[1] or 0),
                        "with_legs": int(entries[2] or 0)},
            "examples": {k: {f"{f:g}": round(float(v[i]), 4) for i, f in enumerate(FRACTIONS)}
                         for k, v in examples.items()}}


__all__ = [
    "CELLS",
    "EDGE",
    "FAR_SIDE",
    "FRACTIONS",
    "INSIDE",
    "NAME",
    "PRIOR",
    "VERSION",
    "Fill",
    "Fills",
    "Table",
    "at_fill",
    "cell",
    "collect",
    "content",
    "decision_legs",
    "entry_legs",
    "estimate",
    "limit_fill",
    "net",
    "observe",
    "record_entries",
    "saved_quotes",
    "session_observations",
    "structure_fill",
    "summary",
    "version_hash",
]
