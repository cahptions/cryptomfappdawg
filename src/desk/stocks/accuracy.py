"""Accuracy of output: the invariants every number the Stocks app records or shows must satisfy (docs/STOCKS_PLAYS.md
→ Accuracy checks).

Each check reads the real databases read-only (stocks-market.db, stocks-research.db, stocks-ledger-paper.db,
stocks-learning-paper.db) and the saved views (stocks-plays.json, stocks-plays-performance.json), and returns how many
records it looked at, how many broke the rule, and up to five examples (ids, symbols and dates; never a key or a
setting). Nothing is changed. ``desk stocks check-accuracy`` (``kestrel check``) prints the report; Evidence → Check
data health shows the counts and its downloadable report lists the examples.

A violation is a fact to explain, not something to hide: each class found so far was traced to the code that produced
it and fixed there (docs/STOCKS_PLAYS.md lists them). Returns are on the money at risk, so a bought position or a
defined-risk structure can never lose more than 100% of it.
"""

from __future__ import annotations

import json
import math
import sqlite3
import statistics
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from itertools import pairwise
from typing import Any

from desk.core.clock import utc_now
from desk.core.paths import DeskPaths
from desk.stocks import calendar as cal
from desk.stocks.paper.fills import OPTION_FEE_PER_CONTRACT as FEE

VERSION = 1
EXAMPLES = 5
RETURN_FLOOR = -1.0 - 1e-6           # -100% of the money at risk
PROBABILITY_SLACK = 1e-6
ORDER_SLACK = 0.0015                 # chances are stored to 3-4 decimals
MONEY_SLACK = 0.011                  # dollars: two-decimal rounding of per-contract figures
PRICE_SLACK = 0.011                  # per share: two-decimal rounding of break-evens
IV_RANGE = (0.005, 5.0)              # 0.5% to 500% a year: anything outside is a bad solve or a bad quote
HISTORY_SESSIONS = 10                # recent sessions whose History views are rebuilt and checked


@dataclass
class Finding:
    """One invariant's result."""
    id: str
    area: str
    title: str
    checked: int = 0
    violations: int = 0
    examples: list[str] = field(default_factory=list)
    skipped: str | None = None
    notes: list[str] = field(default_factory=list)          # facts worth knowing that are not violations

    def look(self, n: int = 1) -> None:
        self.checked += n

    def fail(self, example: str) -> None:
        self.violations += 1
        if len(self.examples) < EXAMPLES:
            self.examples.append(example)

    def expect(self, ok: bool, example: Callable[..., str] | str) -> None:
        self.checked += 1
        if not ok:
            self.fail(example() if callable(example) else example)

    def summary(self) -> dict[str, Any]:
        return {"id": self.id, "area": self.area, "title": self.title, "checked": self.checked,
                "violations": self.violations, "skipped": self.skipped}

    def as_json(self) -> dict[str, Any]:
        return {**self.summary(), "examples": self.examples, "notes": self.notes}


@dataclass
class Sources:
    """Read-only connections (None when a database does not exist yet) and the saved views."""
    paths: DeskPaths | None
    now: datetime
    market: sqlite3.Connection | None = None
    research: sqlite3.Connection | None = None
    ledger: sqlite3.Connection | None = None
    learning: sqlite3.Connection | None = None
    views: dict[str, Any] = field(default_factory=dict)
    history_sessions: int = HISTORY_SESSIONS

    @property
    def today(self) -> date:
        return cal.new_york(self.now).date()


def _connect(path: Any) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


@contextmanager
def open_sources(paths: DeskPaths, now: datetime | None = None, *,
                 history_sessions: int = HISTORY_SESSIONS) -> Iterator[Sources]:
    from desk.stocks.learning import FILE as LEARNING_FILE
    from desk.stocks.repo import LEDGER_FILE, MARKET_FILE, RESEARCH_FILE

    with ExitStack() as stack:
        conns: dict[str, sqlite3.Connection | None] = {}
        for key, name in (("market", MARKET_FILE), ("research", RESEARCH_FILE), ("ledger", LEDGER_FILE),
                          ("learning", LEARNING_FILE)):
            conn = _connect(paths.db_dir / name)
            if conn is not None:
                stack.callback(conn.close)
            conns[key] = conn
        views: dict[str, Any] = {}
        for name in ("plays", "plays-performance"):
            path = paths.home / f"stocks-{name}.json"
            try:
                views[name] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
            except (OSError, ValueError):
                views[name] = None
        yield Sources(paths, now or utc_now(), conns["market"], conns["research"], conns["ledger"],
                      conns["learning"], views, history_sessions)


# ── shared arithmetic ─────────────────────────────────────────────────────────────────────────────
def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def settle_value(legs: Sequence[Mapping[str, Any]], close: float) -> float:
    """Per share at expiration, as held (long +, short −)."""
    total = 0.0
    for leg in legs:
        k = float(leg["strike"])
        intrinsic = max(0.0, close - k) if leg["kind"] == "call" else max(0.0, k - close)
        total += intrinsic if leg["side"] == "buy" else -intrinsic
    return total


def worst_loss(action: str, entry: float, legs: Sequence[Mapping[str, Any]]) -> float:
    """The real worst case per contract, fees included: a bought structure loses what was paid; a sold one its worst
    expiry value (checked at every strike and at zero) less the credit. Infinite for an uncovered short call."""
    fees = FEE * len(legs)
    if action == "BUY":
        return entry * 100 + fees
    strikes = sorted({float(leg["strike"]) for leg in legs})
    points = [0.0, *strikes]
    slope_up = sum((1 if leg["side"] == "buy" else -1) for leg in legs if leg["kind"] == "call")
    if slope_up < 0:
        return math.inf
    worst = min(settle_value(legs, s) for s in points)
    return -(entry + worst) * 100 + fees


def _label(symbol: Any, session: Any, category: Any, rank: Any = None, extra: str = "") -> str:
    day = date.fromordinal(session).isoformat() if isinstance(session, int) else str(session)
    out = f"{symbol} · {category}" + (f" #{rank}" if rank is not None else "") + f" · picked {day}"
    return out + (f" · {extra}" if extra else "")


def _pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


def _settle_day(expiration: date) -> date:
    return expiration if cal.is_trading_day(expiration) else cal.previous_trading_day(expiration)


@dataclass(frozen=True)
class Recorded:
    """A play as recorded: a closing pick (twin 0), its random twin (twin 1) or an identified idea from the archive."""
    session: int
    category: str
    rank: int
    twin: int
    symbol: str
    action: str
    structure: str
    expiration: date
    entry: float
    max_loss: float
    legs: list[dict[str, Any]]
    play: dict[str, Any]
    source: str

    @property
    def name(self) -> str:
        what = "twin of " if self.twin else ""
        return _label(self.symbol, self.session, what + self.category, self.rank, self.structure)


def recorded(research: sqlite3.Connection) -> list[Recorded]:
    from desk.stocks.play_archive import ARCHIVE_CATEGORY

    out = []
    for r in research.execute("SELECT * FROM stk_plays ORDER BY session, category, rank"):
        out.append(Recorded(r["session"], r["category"], r["rank"], 0, r["symbol"], r["action"], r["structure"],
                            date.fromordinal(r["expiration"]), float(r["entry"]), float(r["max_loss"]),
                            json.loads(r["legs_json"]), json.loads(r["play_json"]), "closing"))
    for r in research.execute("SELECT * FROM stk_play_twins ORDER BY session, category, rank"):
        out.append(Recorded(r["session"], r["category"], r["rank"], 1, r["symbol"], r["action"], r["structure"],
                            date.fromordinal(r["expiration"]), float(r["entry"]), float(r["max_loss"]),
                            json.loads(r["legs_json"]), {}, "twin"))
    for r in research.execute("SELECT * FROM stk_play_ideas ORDER BY session, id"):
        play = json.loads(r["play_json"])
        if any(play.get(k) is None for k in ("symbol", "action", "structure", "expiration", "entry", "max_loss",
                                             "legs")):
            continue                      # not a play the app records or marks (an incomplete test record)
        out.append(Recorded(r["session"], ARCHIVE_CATEGORY + r["id"], 1, 0, play["symbol"], play["action"],
                            play["structure"], date.fromisoformat(play["expiration"]), float(play["entry"]),
                            float(play["max_loss"]), play["legs"], play, "identified"))
    return out


def board_plays(board: Mapping[str, Any] | None) -> Iterator[tuple[str, dict[str, Any]]]:
    """Every play on the saved Plays board: (where it is shown, the play)."""
    for c in (board or {}).get("categories") or []:
        for key in ("plays", "budget_plays", "horizon_plays"):
            for p in c.get(key) or []:
                if isinstance(p, dict):
                    yield f"{c.get('id')}/{key}", p


# ── Plays: chances ────────────────────────────────────────────────────────────────────────────────
def probabilities(play: Mapping[str, Any]) -> dict[str, float | None]:
    """Every chance a play carries, by name."""
    out: dict[str, float | None] = {}
    for key in ("itm_market", "itm_history", "itm_kestrel", "itm_kestrel_raw", "profit_kestrel", "touch_kestrel"):
        if key in play and play[key] is not None:
            out[key] = _num(play[key])
    targets = play.get("probability_targets") or {}
    for k, v in (targets.get("returns") or {}).items():
        if v is not None:
            out[f"target{k}"] = _num(v)
    if targets.get("max_gain_probability") is not None:
        out["max_gain"] = _num(targets["max_gain_probability"])
    for event, v in (play.get("calibrated") or {}).items():
        if isinstance(v, dict):
            for part in ("raw", "shown"):
                if v.get(part) is not None:
                    out[f"calibrated.{event}.{part}"] = _num(v[part])
    odds = ((play.get("exit_plan") or {}).get("odds")) or {}
    for key in ("take_profit", "take_profit_raw", "stop", "neither"):
        if odds.get(key) is not None:
            out[f"exit_plan.{key}"] = _num(odds[key])
    return out


# Plays recorded before 2026-11-13 kept two stored errors (calibrated chances out of their necessary order, and a
# break-even on the side of a lopsided condor or butterfly that cannot lose). Their stored numbers are kept as
# recorded and corrected wherever they are displayed (plays.displayed), so they are checked as displayed. Anything
# recorded since, and the live board, is checked exactly as stored.
DISPLAY_CORRECTED_BEFORE = date(2026, 11, 13).toordinal()


def _all_plays(src: Sources) -> Iterator[tuple[str, dict[str, Any]]]:
    from desk.stocks.plays import displayed

    if src.research is not None:
        for r in recorded(src.research):
            if r.play:
                play = {**r.play, "symbol": r.symbol}
                yield r.name, displayed(play) if r.session < DISPLAY_CORRECTED_BEFORE else play
    for where, p in board_plays(src.views.get("plays")):
        yield f"{p.get('symbol')} · board {where} #{p.get('rank')}", p


def check_play_probabilities(src: Sources) -> Finding:
    f = Finding("plays.probabilities", "Plays", "Every chance a play shows is between 0% and 100%")
    for name, play in _all_plays(src):
        for key, value in probabilities(play).items():
            f.expect(value is not None and math.isfinite(value) and -PROBABILITY_SLACK <= value <= 1 + PROBABILITY_SLACK,
                     lambda key=key, value=value, name=name: f"{name}: {key} = {value}")
    return f


def chance_order(play: Mapping[str, Any]) -> list[str]:
    """What breaks the order a play's chances must keep: P(+100%) ≤ P(+50%) ≤ P(+25%) ≤ P(profit at expiry), the
    maximum gain no likelier than any profit, and the exit plan's take-profit and stop chances adding to at most 1 —
    for the model's own numbers and for the calibrated numbers shown."""
    problems = []
    targets = (play.get("probability_targets") or {}).get("returns") or {}
    calibrated = play.get("calibrated") or {}
    for basis, chances in (
            ("model", {k: _num(targets.get(k)) for k in ("100", "50", "25", "0")}
             | {"max": _num((play.get("probability_targets") or {}).get("max_gain_probability"))}),
            ("shown", {k: _num((calibrated.get(e) or {}).get("shown")) for k, e in
                       (("100", "target100"), ("50", "target50"), ("25", "target25"), ("0", "profit"),
                        ("max", "max_gain"))})):
        ladder = [(k, chances[k]) for k in ("100", "50", "25", "0") if chances[k] is not None]
        for (k1, v1), (k2, v2) in pairwise(ladder):
            if v1 is not None and v2 is not None and v1 > v2 + ORDER_SLACK:
                name = {"0": "profit"}.get(k2, f"+{k2}%")
                problems.append(f"{basis}: P(+{k1}%) {v1:.3f} > P({name}) {v2:.3f}")
        top, profit = chances["max"], chances["0"]
        if top is not None and profit is not None and top > profit + ORDER_SLACK:
            problems.append(f"{basis}: P(max gain) {top:.3f} > P(profit) {profit:.3f}")
    odds = ((play.get("exit_plan") or {}).get("odds")) or {}
    take, stop = _num(odds.get("take_profit")), _num(odds.get("stop"))
    if take is not None and stop is not None and take + stop > 1 + ORDER_SLACK:
        problems.append(f"exit plan: take-profit {take:.3f} + stop {stop:.3f} > 1")
    return problems


def check_play_chance_order(src: Sources) -> Finding:
    f = Finding("plays.chance_order", "Plays", "Bigger targets are never likelier: P(+100%) ≤ P(+50%) ≤ P(+25%) ≤ "
                "P(profit at expiry); take-profit plus stop chance ≤ 100%")
    for name, play in _all_plays(src):
        problems = chance_order(play)
        f.expect(not problems, lambda name=name, problems=problems: f"{name}: {'; '.join(problems)}")
    return f


# ── Plays: entry, risk, break-evens, expiry ───────────────────────────────────────────────────────
def risk_problems(action: str, entry: float, max_loss: float, legs: Sequence[Mapping[str, Any]]) -> list[str]:
    problems = []
    if not entry > 0:
        problems.append(f"entry {entry}")
    if not max_loss > 0:
        problems.append(f"max loss {max_loss}")
    if legs:
        worst = worst_loss(action, entry, legs)
        if max_loss + MONEY_SLACK < worst:
            problems.append(f"max loss ${max_loss:,.2f} below the real worst case ${worst:,.2f}")
    return problems


def check_play_risk(src: Sources) -> Finding:
    f = Finding("plays.risk", "Plays", "Entry > 0; maximum loss > 0 and at least the real worst case")
    if src.research is not None:
        for r in recorded(src.research):
            problems = risk_problems(r.action, r.entry, r.max_loss, r.legs)
            f.expect(not problems, lambda r=r, problems=problems: f"{r.name}: {'; '.join(problems)}")
    for where, p in board_plays(src.views.get("plays")):
        entry, loss = _num(p.get("entry")) or 0.0, _num(p.get("max_loss")) or 0.0
        problems = risk_problems(str(p.get("action")), entry, loss, p.get("legs") or [])
        f.expect(not problems, lambda p=p, where=where, problems=problems:
                 f"{p.get('symbol')} · board {where}: {'; '.join(problems)}")
    return f


def breakeven_problems(play: Mapping[str, Any]) -> list[str]:
    """A break-even must be the stock price where the position, held to expiry, neither makes nor loses its entry
    (before fees), so it sits on the correct side of the strike."""
    legs, entry = play.get("legs") or [], _num(play.get("entry"))
    if not legs or entry is None:
        return []
    sign = -1 if play.get("action") == "BUY" else 1
    levels = [b for b in (play.get("breakevens") or [play.get("breakeven")]) if _num(b) is not None]
    problems = []
    for level in levels:
        value = settle_value(legs, float(level)) + sign * entry
        if abs(value) > PRICE_SLACK + 1e-9:
            strikes = "/".join(f"{float(leg['strike']):g}" for leg in legs)
            problems.append(f"break-even {float(level):g} (strikes {strikes}, entry {entry:g}) leaves "
                            f"{value:+.2f} a share at expiry")
    return problems


def check_play_breakevens(src: Sources) -> Finding:
    f = Finding("plays.breakevens", "Plays", "Break-evens on the right side of the strike (no gain, no loss at expiry)")
    for name, play in _all_plays(src):
        if "breakeven" not in play and "breakevens" not in play:
            continue
        problems = breakeven_problems(play)
        f.expect(not problems, lambda name=name, problems=problems: f"{name}: {'; '.join(problems)}")
    return f


def check_play_expiration(src: Sources) -> Finding:
    f = Finding("plays.expiration", "Plays", "Expiration on or after the session it was picked; the board shows no "
                "expired contract")
    if src.research is not None:
        for r in recorded(src.research):
            f.expect(r.expiration.toordinal() >= r.session,
                     lambda r=r: f"{r.name}: expires {r.expiration.isoformat()}")
    board = src.views.get("plays") or {}
    asof = board.get("asof")
    if asof:
        day = date.fromisoformat(str(asof)[:10])
        for where, p in board_plays(board):
            expiry = p.get("expiration")
            f.expect(bool(expiry) and date.fromisoformat(str(expiry)) >= day and (_num(p.get("dte")) or 0) >= 0,
                     lambda p=p, where=where: f"{p.get('symbol')} · board {where}: expires {p.get('expiration')} "
                                              f"on a board for {asof}")
    return f


def check_budget_bands(src: Sources) -> Finding:
    f = Finding("plays.budget_band", "Plays", "Budget plays cost (or risk, if sold) within their stated budget band")
    for where, p in board_plays(src.views.get("plays")):
        band = p.get("budget_band")
        if not where.endswith("budget_plays") or not band:
            continue
        risk = _num(p.get("max_loss") if p.get("action") == "SELL" else p.get("cost"))
        lo, hi = float(band[0]), float(band[1])
        f.expect(risk is not None and lo < risk <= hi + 1e-9,
                 lambda p=p, risk=risk, lo=lo, hi=hi, where=where: f"{p.get('symbol')} {p.get('structure')} · {where}: "
                                                      f"${risk} outside ${lo:g}–${hi:g}")
    return f


def check_horizon_bands(src: Sources) -> Finding:
    from desk.stocks.plays import HORIZONS

    bands = {band: (lo, hi) for band, _label, lo, hi in HORIZONS}
    f = Finding("plays.horizon_band", "Plays", "Expiry filter plays expire inside their band's days-to-expiry window")
    for where, p in board_plays(src.views.get("plays")):
        band = p.get("horizon")
        if not where.endswith("horizon_plays") or band not in bands:
            continue
        lo, hi = bands[band]
        dte = _num(p.get("dte"))
        f.expect(dte is not None and lo <= dte <= hi,
                 lambda p=p, lo=lo, hi=hi, band=band, where=where: f"{p.get('symbol')} · {where} {band}: {p.get('dte')} days, "
                                                      f"band {lo}–{hi}")
    if src.research is not None:
        for r in src.research.execute("SELECT session, category, rank, symbol, expiration, play_json FROM stk_plays "
                                      "WHERE instr(category, '@') > 0"):
            band = r["category"].split("@", 1)[1]
            if band not in bands:
                continue
            lo, hi = bands[band]
            dte = _num(json.loads(r["play_json"]).get("dte"))
            dte = r["expiration"] - r["session"] if dte is None else dte
            f.expect(lo <= dte <= hi, lambda r=r, dte=dte, lo=lo, hi=hi:
                     _label(r["symbol"], r["session"], r["category"], r["rank"], f"{dte:g} days, band {lo}–{hi}"))
    return f


# ── Plays: marks and outcomes ─────────────────────────────────────────────────────────────────────
def _expirations(research: sqlite3.Connection) -> dict[tuple[int, str, int, int], tuple[Recorded, date]]:
    return {(r.session, r.category, r.rank, r.twin): (r, _settle_day(r.expiration)) for r in recorded(research)}


def check_mark_floor(src: Sources) -> Finding:
    f = Finding("plays.mark_floor", "Plays", "No mark loses more than 100% of the money at risk (a bought option or "
                "debit structure loses at most what was paid; a credit structure at most its maximum loss)")
    if src.research is None:
        f.skipped = "no research database"
        return f
    known = _expirations(src.research)
    for table in ("stk_play_marks", "stk_play_marks_provisional"):
        status = "status" if table == "stk_play_marks" else "'estimated' AS status"
        for m in src.research.execute(f"SELECT session, category, rank, twin, day, {status}, pnl, pnl_pct "  # noqa: S608
                                      f"FROM {table}"):
            r = known.get((m["session"], m["category"], m["rank"], m["twin"]))
            f.expect(m["pnl_pct"] >= RETURN_FLOOR, lambda m=m, r=r, table=table: (
                (r[0].name if r else _label("?", m["session"], m["category"], m["rank"]))
                + f" · {m['status']} mark {date.fromordinal(m['day']).isoformat()}: {_pct(m['pnl_pct'])}"
                + (" (provisional)" if table.endswith("provisional") else "")))
    return f


def check_mark_dates(src: Sources) -> Finding:
    f = Finding("plays.mark_dates", "Plays", "Settled marks only on the settlement session; no mark after it")
    if src.research is None:
        f.skipped = "no research database"
        return f
    known = _expirations(src.research)
    for m in src.research.execute("SELECT session, category, rank, twin, day, status FROM stk_play_marks"):
        found = known.get((m["session"], m["category"], m["rank"], m["twin"]))
        if found is None:
            continue
        r, settles = found
        day = date.fromordinal(m["day"])
        ok = day <= settles and (m["status"] != "settled" or day == settles) and m["day"] >= m["session"]
        f.expect(ok, lambda r=r, day=day, settles=settles, m=m:
                 f"{r.name}: {m['status']} mark on {day.isoformat()}, settles {settles.isoformat()}")
    return f


def _official_close(src: Sources, symbol: str, day: date) -> float | None:
    if src.market is not None:
        row = src.market.execute("SELECT close FROM stk_bars WHERE symbol = ? AND day = ?",
                                 (symbol, day.toordinal())).fetchone()
        if row is not None and row[0]:
            return float(row[0])
    if src.research is not None:
        row = src.research.execute("SELECT close FROM stk_track_closes WHERE symbol = ? AND session = ?",
                                   (symbol, day.toordinal())).fetchone()
        if row is not None and row[0]:
            return float(row[0])
    return None


def check_settlement_waits(src: Sources) -> Finding:
    f = Finding("plays.settlement", "Plays", "A play is settled within one trading day of its settlement session once "
                "that session's official close is stored (no stale 'awaiting expiry close')")
    if src.research is None:
        f.skipped = "no research database"
        return f
    cutoff = cal.previous_trading_day(src.today)                # settled by now if it settled before this day
    settled = {(r[0], r[1], r[2], r[3]) for r in src.research.execute(
        "SELECT session, category, rank, twin FROM stk_play_marks WHERE status = 'settled'")}
    quarantined = {(r[0], r[1], r[2], r[3]) for r in src.research.execute(
        "SELECT session, category, rank, twin FROM stk_play_quarantine")}
    for key, (r, settles) in _expirations(src.research).items():
        if "@" in r.category or settles >= cutoff or key in settled or key in quarantined:
            continue
        if src.market is not None and src.market.execute(
                "SELECT 1 FROM stk_splits WHERE symbol = ? AND execution_date > ? AND execution_date <= ?",
                (r.symbol, r.session, settles.toordinal())).fetchone():
            continue                          # a split ends the record; it is never settled on the old strikes
        if r.source == "identified":
            continue                          # identified ideas are settled when viewed; the archive check covers them
        close = _official_close(src, r.symbol, settles)
        f.expect(close is None, lambda r=r, settles=settles, close=close:
                 f"{r.name}: settles {settles.isoformat()} (close {close:g} stored) but has no settled mark")
    return f


def _outcome_net(action: str, entry: float, max_loss: float, payoff: float, legs: int) -> float:
    fees = FEE * legs / 100
    if action == "BUY":
        return (payoff - entry - fees) / (entry + fees)
    return (entry + payoff - fees) / (max_loss / 100)


def check_outcomes(src: Sources) -> Finding:
    f = Finding("plays.outcomes", "Plays", "Graded outcomes: settled on the settlement session's official close, payoff "
                "and net return consistent with it, never below -100%")
    if src.research is None:
        f.skipped = "no research database"
        return f
    for o in src.research.execute("SELECT o.*, p.symbol, p.action, p.entry, p.max_loss, p.legs_json, p.expiration, "
                                  "p.event, p.event_kind, p.event_strike, p.event_width FROM stk_play_outcomes o "
                                  "JOIN stk_plays p USING (session, category, rank)"):
        legs = json.loads(o["legs_json"])
        name = _label(o["symbol"], o["session"], o["category"], o["rank"])
        problems = []
        payoff = settle_value(legs, o["underlying_close"])
        if abs(payoff - o["payoff"]) > 1e-6:
            problems.append(f"payoff {o['payoff']:g} ≠ {payoff:g} on the {o['underlying_close']:g} close")
        net = _outcome_net(o["action"], o["entry"], o["max_loss"], o["payoff"], len(legs))
        if abs(net - o["net_return"]) > 1e-6:
            problems.append(f"net return {_pct(o['net_return'])} ≠ {_pct(net)} from its payoff")
        if o["net_return"] < RETURN_FLOOR:
            problems.append(f"net return {_pct(o['net_return'])}")
        settles = _settle_day(date.fromordinal(o["expiration"]))
        close = None if src.market is None else src.market.execute(
            "SELECT close FROM stk_bars WHERE symbol = ? AND day = ?", (o["symbol"], settles.toordinal())).fetchone()
        if close is not None and abs(float(close[0]) - o["underlying_close"]) > 1e-6:
            problems.append(f"graded on a {o['underlying_close']:g} close, but the {settles.isoformat()} close is "
                            f"{float(close[0]):g}")
        elif close is None and src.market is not None:
            problems.append(f"graded without the {settles.isoformat()} close")
        hit = (abs(o["underlying_close"] - o["event_strike"]) > o["event_width"] if o["event"] == "move" else
               o["underlying_close"] > o["event_strike"] if o["event_kind"] == "call" else
               o["underlying_close"] < o["event_strike"])
        if bool(o["hit"]) != hit:
            problems.append("hit flag disagrees with the close")
        f.expect(not problems, lambda name=name, problems=problems: f"{name}: {'; '.join(problems)}")
    return f


# ── History and Performance ───────────────────────────────────────────────────────────────────────
def summary_problems(summary: Mapping[str, Any] | None, rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """A History summary must be the arithmetic of the rows it lists."""
    scored = [r for r in rows if r.get("latest")]
    if summary is None:
        return ["summary missing though plays have values"] if scored else []
    if not scored:
        return ["summary shown with no valued play"]
    pcts = [float(r["latest"]["pct"]) for r in scored]
    problems = []
    if summary["plays"] != len(pcts):
        problems.append(f"{summary['plays']} plays counted, {len(pcts)} listed with a value")
    if abs(summary["average"] - statistics.fmean(pcts)) > 1e-4:
        problems.append(f"average {_pct(summary['average'])} ≠ {_pct(statistics.fmean(pcts))}")
    if abs(summary["median"] - statistics.median(pcts)) > 1e-4:
        problems.append(f"median {_pct(summary['median'])} ≠ {_pct(statistics.median(pcts))}")
    if abs(summary["hit_rate"] - sum(p > 0 for p in pcts) / len(pcts)) > 1e-3:
        problems.append(f"in profit {summary['hit_rate']:.3f} ≠ {sum(p > 0 for p in pcts)} of {len(pcts)}")
    hundred = summary.get("hundred_each") or {}
    if hundred:
        value = sum(100 * (1 + p) for p in pcts)
        if hundred.get("invested") != 100 * len(pcts) or abs(hundred.get("value", 0) - value) > 0.02 * len(pcts):
            problems.append(f"$100 in each: ${hundred.get('value')} of ${hundred.get('invested')}, rows give "
                            f"${value:,.2f} of ${100 * len(pcts)}")
        if abs((hundred.get("value", 0) - hundred.get("invested", 0)) / max(hundred.get("invested") or 1, 1)
               - summary["average"]) > 1e-3:
            problems.append("$100 in each disagrees with the average")
    best, worst = (summary.get("best") or {}).get("pct"), (summary.get("worst") or {}).get("pct")
    if best is not None and worst is not None and (best < worst or abs(best - max(pcts)) > 1e-6
                                                   or abs(worst - min(pcts)) > 1e-6):
        problems.append(f"best {_pct(best)} / worst {_pct(worst)} not the rows' {_pct(max(pcts))} / {_pct(min(pcts))}")
    twins = [(float(r["latest"]["pct"]), float(r["twin"]["pct"])) for r in scored
             if (r.get("twin") or {}).get("pct") is not None]
    if summary.get("twins_average") is not None:
        paired = summary.get("twins_paired") or {}
        if not twins:
            problems.append("random twins shown with no twin value")
        elif abs(summary["twins_average"] - statistics.fmean(t for _, t in twins)) > 1e-4:
            problems.append("random twins average is not the listed twins' average")
        elif len(twins) != len(pcts) and (paired.get("plays") != len(twins) or paired.get("average") is None
                                          or abs(paired["average"] - statistics.fmean(p for p, _ in twins)) > 1e-4):
            problems.append(f"random twins ({len(twins)}) compared with all {len(pcts)} plays, not the same pairs")
    return problems


def row_problems(row: Mapping[str, Any]) -> list[str]:
    problems = []
    latest = row.get("latest") or {}
    pct = _num(latest.get("pct"))
    if pct is not None and pct < RETURN_FLOOR:
        problems.append(f"latest {_pct(pct)}")
    peak, worst = _num(row.get("peak")), _num(row.get("worst"))
    if peak is not None and worst is not None and peak < worst - 1e-9:
        problems.append(f"peak {_pct(peak)} below worst {_pct(worst)}")
    if worst is not None and worst < RETURN_FLOOR:
        problems.append(f"worst {_pct(worst)}")
    if pct is not None and latest.get("status") in ("quoted", "live", "settled") and peak is not None \
            and worst is not None and not worst - 1e-6 <= pct <= peak + 1e-6:
        problems.append(f"latest {_pct(pct)} outside its own peak {_pct(peak)} / worst {_pct(worst)}")
    if (row.get("days_held") or 0) < 0:
        problems.append(f"{row.get('days_held')} days held")
    if not (_num(row.get("cost")) or 0) > 0:
        problems.append(f"money at risk {row.get('cost')}")
    first = (row.get("first_seen") or {}).get("pct")
    if first is not None and first < RETURN_FLOOR:
        problems.append(f"first-seen basis {_pct(first)}")
    return problems


def _history_views(src: Sources) -> Iterator[tuple[str, dict[str, Any]]]:
    from desk.stocks import play_archive, play_history

    assert src.research is not None
    sessions = [date.fromordinal(r[0]) for r in src.research.execute(
        "SELECT DISTINCT session FROM stk_plays WHERE instr(category, '@') = 0 UNION "
        "SELECT DISTINCT session FROM stk_play_ideas ORDER BY 1 DESC LIMIT ?", (src.history_sessions,))]
    for session in sessions:
        yield f"History {session.isoformat()} (closing picks)", play_history.session_view(src.research, session, src.now)
        yield (f"History {session.isoformat()} (every identified play)",
               play_archive.session_view(src.research, session, src.now, src.market))


def check_history(src: Sources) -> Finding:
    f = Finding("history.summaries", "History", "History summaries are the arithmetic of their rows (average, median, "
                "in profit, $100 in each, best ≥ worst, twins over the same pairs); rows' peaks, lows and days held "
                "are possible")
    if src.research is None:
        f.skipped = "no research database"
        return f
    for name, view in _history_views(src):
        every = []
        for c in view.get("categories") or []:
            every.extend(c["plays"])
            problems = summary_problems(c.get("summary"), c["plays"])
            f.expect(not problems, lambda name=name, c=c, problems=problems:
                     f"{name} · {c['id']} summary: {'; '.join(problems)}")
            for row in c["plays"]:
                problems = row_problems(row)
                f.expect(not problems, lambda name=name, row=row, problems=problems:
                         f"{name} · {row.get('label')}: {'; '.join(problems)}")
        problems = summary_problems(view.get("summary"), every)
        f.expect(not problems, lambda name=name, problems=problems: f"{name} summary: {'; '.join(problems)}")
    return f


def check_archive_settled(src: Sources) -> Finding:
    f = Finding("history.awaiting_close", "History", "No identified play shows 'awaiting expiry close' more than one "
                "trading day after its settlement session when that close is stored")
    if src.research is None:
        f.skipped = "no research database"
        return f
    cutoff = cal.previous_trading_day(src.today)
    for name, view in _history_views(src):
        if "identified" not in name:
            continue
        for c in view.get("categories") or []:
            for row in c["plays"]:
                if "awaiting" not in str(row.get("state_label", "")).lower():
                    continue
                settles = _settle_day(date.fromisoformat(row["expiration"]))
                close = _official_close(src, row["symbol"], settles)
                f.expect(settles >= cutoff or close is None, lambda name=name, row=row, settles=settles, close=close:
                         f"{name} · {row.get('label')}: '{row.get('state_label')}' though the {settles.isoformat()} "
                         f"close {close} is stored")
    return f


def stats_problems(s: Mapping[str, Any] | None, *, returns: bool = True) -> list[str]:
    if not s:
        return []
    problems = []
    if not s.get("n", 0) >= 1:
        problems.append(f"n = {s.get('n')}")
    if not 0 <= s.get("hit_rate", 0) <= 1:
        problems.append(f"in profit {s.get('hit_rate')}")
    if returns:
        for key in ("mean", "median"):
            if _num(s.get(key)) is not None and s[key] < RETURN_FLOOR:
                problems.append(f"{key} {_pct(s[key])}")
    return problems


def performance_problems(view: Mapping[str, Any]) -> list[str]:
    problems: list[str] = []

    def horizons(where: str, items: Sequence[Mapping[str, Any]]) -> None:
        for h in items:
            for part in ("plays", "twins", "paired"):
                problems.extend(f"{where} {h.get('label')} {part}: {p}" for p in stats_problems(h.get(part)))
            plays, twins = h.get("plays") or {}, h.get("twins") or {}
            if twins and plays and twins.get("n") != plays.get("n"):
                paired = h.get("paired") or {}
                if paired.get("n") != twins.get("n"):
                    problems.append(f"{where} {h.get('label')}: random twins (n {twins.get('n')}) compared with "
                                    f"{plays.get('n')} plays, not the same pairs")

    horizons("Overall", view.get("overall") or [])
    for c in view.get("categories") or []:
        for v in c.get("versions") or []:
            horizons(f"{c.get('id')} {str(v.get('method'))[:8]}", v.get("horizons") or [])
            if not 0 <= v.get("open", 0) <= v.get("plays", 0):
                problems.append(f"{c.get('id')}: {v.get('open')} open of {v.get('plays')}")
    for key in ("dte", "price"):
        for b in view.get(key) or []:
            for part in ("week", "expiry", "twins_expiry", "paired_expiry"):
                problems.extend(f"{key} {b.get('label')} {part}: {p}" for p in stats_problems(b.get(part)))
            twins, expiry = b.get("twins_expiry") or {}, b.get("expiry") or {}
            if twins and expiry and twins.get("n") != expiry.get("n") and \
                    (b.get("paired_expiry") or {}).get("n") != twins.get("n"):
                problems.append(f"{key} {b.get('label')}: random twins (n {twins.get('n')}) compared with "
                                f"{expiry.get('n')} plays, not the same pairs")
    if not 0 <= view.get("resolved", 0) <= view.get("plays", 0):
        problems.append(f"{view.get('resolved')} resolved of {view.get('plays')} plays")
    for curve, rows in (view.get("cumulative") or {}).items():
        settled = 0
        for r in rows:
            if r["settled"] < settled:
                problems.append(f"cumulative {curve} {r['t']}: settled count went down")
            settled = r["settled"]
            if abs(r["invested"] - 100 * r["settled"]) > 0.01:
                problems.append(f"cumulative {curve} {r['t']}: ${r['invested']} invested for {r['settled']} plays")
            if r["picks"] < -r["invested"] - 0.01:
                problems.append(f"cumulative {curve} {r['t']}: picks ${r['picks']} lost more than ${r['invested']}")
            for base, count in (("twins", "twinned"), ("spy", "spy_settled")):
                if count not in r and r.get(base):
                    problems.append(f"cumulative {curve} {r['t']}: {base} not counted over the same plays")
                elif count in r and (r[count] > r["settled"] or r[base] < -100 * r[count] - 0.01):
                    problems.append(f"cumulative {curve} {r['t']}: {base} ${r[base]} over {r[count]} plays")
    for key, block in (view.get("calibration") or {}).items():
        items = block.values() if key == "events" else [block]
        for item in items:
            for point in item.get("points") or []:
                if not (0 <= point["predicted"] <= 1 and 0 <= point["observed"] <= 1 and point["n"] >= 1):
                    problems.append(f"calibration {key} {point.get('bucket')}: {point}")
            if item.get("brier") is not None and not 0 <= item["brier"] <= 1:
                problems.append(f"calibration {key}: Brier {item['brier']}")
    return problems


def check_performance(src: Sources) -> Finding:
    f = Finding("performance.view", "Performance", "Performance: counts and rates possible, returns ≥ -100%, $100 "
                "curves invested = 100 × settled, twins and SPY compared over the same plays")
    view = src.views.get("plays-performance")
    if not view:
        f.skipped = "no saved Performance view"
        return f
    for p in performance_problems(view):
        f.fail(p)
    f.look(1 + len(view.get("categories") or []))
    return f


# ── PAPER and Learning books ──────────────────────────────────────────────────────────────────────
def _books(src: Sources) -> Iterator[tuple[str, sqlite3.Connection]]:
    if src.ledger is not None:
        yield "PAPER", src.ledger
    if src.learning is not None:
        yield "Learning", src.learning


def check_book_cash(src: Sources) -> Finding:
    f = Finding("books.cash", "PAPER and Learning", "Cash never goes negative; equity = cash + marked positions")
    for book, conn in _books(src):
        balance: dict[str, int] = {}
        low: dict[str, tuple[int, str]] = {}
        for r in conn.execute("SELECT account, at, amount_micros FROM stk_cash ORDER BY id"):
            balance[r["account"]] = balance.get(r["account"], 0) + r["amount_micros"]
            if balance[r["account"]] < low.get(r["account"], (0, ""))[0]:
                low[r["account"]] = (balance[r["account"]], r["at"])
        for account in balance:
            f.expect(account not in low, lambda account=account, book=book, low=low:
                     f"{book} {account}: cash ${low[account][0] / 1e6:,.2f} at {low[account][1]}")
        for r in conn.execute("SELECT * FROM stk_equity"):
            f.expect(r["equity_micros"] == r["cash_micros"] + r["positions_micros"], lambda r=r, book=book:
                     f"{book} {r['account']} {date.fromordinal(r['day']).isoformat()}: equity ≠ cash + positions")
        has_nav = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'stk_learning_nav'").fetchone()
        if has_nav:
            for r in conn.execute("SELECT * FROM stk_learning_nav"):
                f.expect(abs(r["pnl"] - (r["equity"] - r["contributed"])) < 0.01, lambda r=r, book=book:
                         f"{book} {r['account']} {r['at']}: P/L ≠ equity − contributed")
    return f


def position_problems(p: sqlite3.Row) -> list[str]:
    problems = []
    legs = json.loads(p["legs_json"])
    for leg in legs:                      # a PAPER leg's quantity is signed (short < 0); zero is no position
        if not _num(leg.get("quantity")):
            problems.append(f"leg {leg.get('occ') or leg.get('symbol')} quantity {leg.get('quantity')}")
    if p["entry_fees_micros"] < 0:
        problems.append("negative entry fees")
    bought = p["entry_cost_micros"] > 0
    long_only = all((_num(leg.get("quantity")) or 0) > 0 for leg in legs)
    if p["asset"] == "option" and long_only and p["last_value_micros"] is not None and p["last_value_micros"] < 0:
        problems.append(f"bought option marked at ${p['last_value_micros'] / 1e6:,.2f}")
    if p["peak_value_micros"] is not None and p["last_value_micros"] is not None and p["state"] == "OPEN" \
            and p["peak_value_micros"] < p["last_value_micros"]:
        problems.append("peak value below the current value")
    if p["state"] == "CLOSED":
        if p["exit_day"] is None or p["exit_day"] < p["entry_day"] or (p["closed_at"] or "") < p["opened_at"]:
            problems.append(f"closed {p['closed_at']} before it opened {p['opened_at']}")
        if (p["exit_fees_micros"] or 0) < 0:
            problems.append("negative exit fees")
        pnl = (p["exit_credit_micros"] or 0) - (p["exit_fees_micros"] or 0) - p["entry_cost_micros"] \
            - p["entry_fees_micros"]
        if p["asset"] == "option":
            limit = (p["entry_cost_micros"] + p["entry_fees_micros"] if bought else (p["max_loss_micros"] or 0)) \
                + (p["exit_fees_micros"] or 0)
            if (bought or long_only) and pnl < -limit - 10_000:
                problems.append(f"lost ${-pnl / 1e6:,.2f}, more than its cost and fees ${limit / 1e6:,.2f}")
    return problems


def check_book_positions(src: Sources) -> Finding:
    f = Finding("books.positions", "PAPER and Learning", "Positions: quantity > 0, never closed before opened, a bought "
                "option never loses more than its cost and fees")
    for book, conn in _books(src):
        for p in conn.execute("SELECT * FROM stk_positions"):
            problems = position_problems(p)
            f.expect(not problems, lambda p=p, book=book, problems=problems:
                     f"{book} {p['account']} {p['symbol']} {p['structure']} ({p['id']}): {'; '.join(problems)}")
    return f


def check_book_fills(src: Sources) -> Finding:
    """PAPER fills happen at prices that existed: a stock between the day's low and high (after the documented
    slippage), an option leg at a quote stored for that contract on the entry day, never below its bid."""
    from desk.stocks.paper.fills import SLIPPAGE_OTHER

    f = Finding("books.fills", "PAPER and Learning", "Fills only at prices that existed: stocks inside the day's "
                "range (after slippage), options at a stored quote of that contract on the entry day")
    if src.ledger is None or src.market is None:
        f.skipped = "no PAPER book or market database"
        return f
    chains: dict[tuple[str, int], dict[str, Any]] = {}

    def chain(symbol: str, day: int) -> dict[str, Any]:
        if (symbol, day) not in chains:
            from desk.stocks.repo import _unpack

            quotes: dict[str, Any] = {}
            for r in src.market.execute("SELECT chain_zlib FROM stk_option_chains WHERE underlying = ? AND day = ?",  # type: ignore[union-attr]
                                        (symbol, day)):
                for q in _unpack(r[0]):
                    quotes[q[0]] = q
            chains[(symbol, day)] = quotes
        return chains[(symbol, day)]

    for p in src.ledger.execute("SELECT * FROM stk_positions"):
        legs = json.loads(p["legs_json"])
        name = f"PAPER {p['account']} {p['symbol']} {p['structure']} ({p['id']})"
        if p["asset"] == "stock":
            bar = src.market.execute("SELECT open, high, low FROM stk_bars WHERE symbol = ? AND day = ?",
                                     (p["symbol"], p["entry_day"])).fetchone()
            price = _num(legs[0].get("entry_price")) if legs else None
            if bar is None or price is None:
                continue
            f.expect(bar["low"] * (1 - SLIPPAGE_OTHER) - 1e-6 <= price <= bar["high"] * (1 + SLIPPAGE_OTHER) + 1e-6,
                     lambda name=name, price=price, bar=bar:
                     f"{name}: bought at {price:g} outside the day's {bar['low']:g}–{bar['high']:g}")
            continue
        quotes = chain(p["symbol"], p["entry_day"])
        for leg in legs:                  # bought at the ask, sold at the bid: inside that day's quote
            q = quotes.get(leg.get("occ"))
            price = _num(leg.get("entry_price"))
            f.expect(q is not None and q[3] is not None and q[4] is not None and price is not None
                     and q[3] - 1e-9 <= price <= q[4] + 1e-9,
                     lambda name=name, leg=leg, q=q: f"{name}: {leg.get('occ')} filled at {leg.get('entry_price')} "
                                                     + ("with no stored quote that day" if q is None else
                                                        f"against a {q[3]}/{q[4]} quote"))
    return f


def check_learning_returns(src: Sources) -> Finding:
    f = Finding("books.learning_returns", "PAPER and Learning", "Learning outcomes and checkpoints (long options only) "
                "never return less than -100% of the entry cost and fees")
    if src.learning is None:
        f.skipped = "no Learning book"
        return f
    for r in src.learning.execute("SELECT o.position_id, o.experiment_id, o.net_return, o.matured_at, p.symbol FROM "
                                  "stk_learning_outcomes o JOIN stk_positions p ON p.id = o.position_id"):
        f.expect(r["net_return"] >= RETURN_FLOOR, lambda r=r: f"Learning {r['symbol']} {r['experiment_id']} "
                                                              f"({r['position_id']}): outcome {_pct(r['net_return'])}")
    for r in src.learning.execute("SELECT c.position_id, c.days, c.net_return, c.value, p.symbol FROM "
                                  "stk_learning_checkpoints c JOIN stk_positions p ON p.id = c.position_id"):
        f.expect(r["net_return"] >= RETURN_FLOOR and r["value"] >= -1e-9, lambda r=r:
                 f"Learning {r['symbol']} ({r['position_id']}) {r['days']}-day checkpoint: value ${r['value']:,.2f}, "
                 f"{_pct(r['net_return'])}")
    return f


# ── prices and options data ───────────────────────────────────────────────────────────────────────
def check_prices(src: Sources) -> Finding:
    f = Finding("data.prices", "Prices and options", "Daily bars: close > 0, low ≤ open/close ≤ high, volume ≥ 0")
    if src.market is None:
        f.skipped = "no market database"
        return f
    n = src.market.execute("SELECT COUNT(*) FROM stk_bars").fetchone()[0]
    f.look(n)
    for r in src.market.execute("SELECT symbol, day, open, high, low, close, volume FROM stk_bars WHERE close <= 0 OR "
                                "open <= 0 OR low > open + 1e-9 OR low > close + 1e-9 OR high < open - 1e-9 OR "
                                "high < close - 1e-9 OR low > high OR volume < 0 LIMIT 100000"):
        f.fail(f"{r['symbol']} {date.fromordinal(r['day']).isoformat()}: O {r['open']:g} H {r['high']:g} "
               f"L {r['low']:g} C {r['close']:g} V {r['volume']:g}")
    return f


def check_option_quotes(src: Sources) -> Finding:
    f = Finding("data.options", "Prices and options", "Option quotes: 0 ≤ bid ≤ ask; implied volatility plausible; "
                "no contract stored after its expiry")
    if src.market is None:
        f.skipped = "no market database"
        return f
    from desk.stocks.repo import _unpack

    crossed = 0
    for r in src.market.execute("SELECT underlying, day, expiration, chain_zlib FROM stk_option_chains ORDER BY day DESC "
                                "LIMIT 5000"):
        day = date.fromordinal(r["day"])
        f.expect(r["expiration"] >= r["day"], lambda r=r, day=day:
                 f"{r['underlying']} chain of {day.isoformat()} holds the expired "
                 f"{date.fromordinal(r['expiration']).isoformat()} expiry")
        for q in _unpack(r["chain_zlib"]):
            bid, ask = q[3], q[4]
            # Stored chains keep Schwab's quotes exactly as received; a crossed one (bid above ask, usually a stale
            # side) is rejected wherever quotes are used (plays.table, activity.qualified_quotes), so it is noted,
            # not failed. A negative price is never valid.
            f.expect(bid is None or ask is None or (bid >= 0 and ask >= 0), lambda q=q, day=day:
                     f"{q[0]} {day.isoformat()}: bid {q[3]} ask {q[4]}")
            if bid is not None and ask is not None and bid > ask + 1e-9:
                crossed += 1
    if crossed:
        f.notes.append(f"{crossed:,} stored chain quotes are crossed (bid above ask) as Schwab sent them; Plays and "
                       "options activity skip them")
    if src.research is not None:
        for r in src.research.execute("SELECT occ, session, status, bid, ask, iv FROM stk_track_days"):
            problems = []
            if r["bid"] is not None and r["ask"] is not None and not 0 <= r["bid"] <= r["ask"] + 1e-9:
                problems.append(f"bid {r['bid']} ask {r['ask']}")
            if r["iv"] is not None and r["status"] == "quoted" and not IV_RANGE[0] <= r["iv"] <= IV_RANGE[1]:
                problems.append(f"implied volatility {r['iv']:.4f}")
            f.expect(not problems, lambda r=r, problems=problems:
                     f"{r['occ']} {date.fromordinal(r['session']).isoformat()} ({r['status']}): {'; '.join(problems)}")
    for where, p in board_plays(src.views.get("plays")):
        iv = _num(p.get("iv"))
        f.expect(iv is None or IV_RANGE[0] <= iv <= IV_RANGE[1], lambda p=p, where=where:
                 f"{p.get('symbol')} · board {where}: implied volatility {p.get('iv')}")
        for leg in p.get("legs") or []:
            bid, ask = _num(leg.get("bid")), _num(leg.get("ask"))
            f.expect(bid is None or ask is None or 0 <= bid <= ask + 1e-9, lambda leg=leg, where=where:
                     f"{leg.get('occ')} · board {where}: bid {leg.get('bid')} ask {leg.get('ask')}")
    return f


# ── Learning report / Evidence ────────────────────────────────────────────────────────────────────
COUNT_KEYS = frozenset({"n", "count", "trades", "plays", "positions", "open", "closed", "filled", "resolved",
                        "sessions", "blocks", "outcomes", "fills", "settled", "requested_units", "filled_units",
                        "experiments", "days", "reached", "wins", "losses", "entries", "exits"})
RETURN_KEYS = frozenset({"net_return", "mean_return", "median_return", "average_return", "return", "mean", "median",
                         "best", "worst", "expected_net_return"})


def walk_numbers(value: Any, path: str = "") -> Iterator[tuple[str, str, float]]:
    """(path, key, number) for every number in a nested report."""
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                yield f"{path}.{k}" if path else str(k), str(k), float(v)
            else:
                yield from walk_numbers(v, f"{path}.{k}" if path else str(k))
    elif isinstance(value, list):
        for i, v in enumerate(value[:2000]):
            yield from walk_numbers(v, f"{path}[{i}]")


def report_problems(report: Any) -> list[str]:
    problems = []
    for where, key, x in walk_numbers(report):
        if not math.isfinite(x):
            problems.append(f"{where} = {x}")
        elif key in COUNT_KEYS and x < 0:
            problems.append(f"{where} = {x:g} (a count)")
        elif key in RETURN_KEYS and "return" in where.lower() and x < RETURN_FLOOR:
            problems.append(f"{where} = {_pct(x)} (a return on money at risk)")
    return problems


def check_learning_report(src: Sources) -> Finding:
    f = Finding("learning.report", "Learning and Evidence", "Learning report: counts non-negative, returns on money at "
                "risk ≥ -100%, every number finite")
    if src.paths is None or src.learning is None:
        f.skipped = "no Learning book"
        return f
    from desk.stocks import learning_report

    report = learning_report.build(src.paths, src.now)
    for p in report_problems(report):
        f.fail(f"Learning report {p}")
    f.look()
    return f


# ── the Learner's experience log ─────────────────────────────────────────────────────────────────
LEARNER_GROUPS = 3000                # the newest candidate groups whose rows and labels are checked


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def check_learner_features(src: Sources) -> Finding:
    f = Finding("learner.features", "Learner", "Learner candidates: every feature finite (or missing), entry and money "
                "at risk positive, every leg's expiry after the decision")
    if src.research is None or not _has_table(src.research, "stk_learn_candidates"):
        f.skipped = "no Learner records"
        return f
    from desk.stocks.learner.experience import unpack

    for r in src.research.execute("SELECT source, session, symbol, rows_zlib FROM stk_learn_candidates ORDER BY session "
                                  "DESC LIMIT ?", (LEARNER_GROUPS,)):
        g = unpack(r["rows_zlib"])
        where = f"{r['source']} {r['symbol']} {date.fromordinal(r['session']).isoformat()}"
        bad = [k for part in (g["stock"], g["market"]) for k, v in part.items()
               if v is not None and (not isinstance(v, (int, float)) or not math.isfinite(v))]
        f.expect(not bad, lambda where=where, bad=bad: f"{where}: stock or market feature not finite: {', '.join(bad[:3])}")
        for row in g["rows"]:
            problems = [k for k, v in row["x"].items()
                        if v is not None and (not isinstance(v, (int, float)) or not math.isfinite(v))]
            if not row["entry"] > 0 or not row["max_loss"] > 0:
                problems.append(f"entry {row['entry']} / at risk {row['max_loss']}")
            if row["expiration"] <= r["session"]:
                problems.append("expires on or before its decision")
            f.expect(not problems, lambda where=where, row=row, problems=problems:
                     f"{where} {row['key'] or row['origin']}: {'; '.join(problems[:3])}")
    return f


def check_learner_labels(src: Sources) -> Finding:
    f = Finding("learner.labels", "Learner", "Learner labels: returns ≥ -100% of the money at risk, finite, dated after "
                "their candidate's decision and written after it")
    if src.research is None or not _has_table(src.research, "stk_learn_labels"):
        f.skipped = "no Learner records"
        return f
    from desk.stocks.learner.labels import unpack_labels

    for r in src.research.execute(
            "SELECT l.source, l.session, l.symbol, l.checkpoint, l.day, l.labelled_at, l.labels_zlib, c.decided_at FROM "
            "stk_learn_labels l LEFT JOIN stk_learn_candidates c ON c.source=l.source AND c.session=l.session AND "
            "c.symbol=l.symbol AND c.actions_version=l.actions_version ORDER BY l.session DESC LIMIT ?",
            (LEARNER_GROUPS * 9,)):
        where = f"{r['source']} {r['symbol']} {date.fromordinal(r['session']).isoformat()} {r['checkpoint']}"
        problems = []
        if r["decided_at"] is None:
            problems.append("no candidate group")
        elif r["day"] <= r["session"] or r["labelled_at"] < r["decided_at"]:
            problems.append(f"label of {date.fromordinal(r['day']).isoformat()} written {r['labelled_at'][:16]} "
                            f"for a decision at {r['decided_at'][:16]}")
        for x in unpack_labels(r["labels_zlib"]):
            values = [x[1], x[2], x[3], *x[6]] if r["checkpoint"].startswith("exp:") else [x[2], x[3]]
            for v in values:
                if v is not None and (not math.isfinite(v) or v < RETURN_FLOOR):
                    problems.append(f"row {x[0]}: return {v}")
                    break
        f.expect(not problems, lambda where=where, problems=problems: f"{where}: {'; '.join(problems[:3])}")
    return f


CHECKS: tuple[Callable[[Sources], Finding], ...] = (
    check_play_probabilities, check_play_chance_order, check_play_risk, check_play_breakevens, check_play_expiration,
    check_budget_bands, check_horizon_bands, check_mark_floor, check_mark_dates, check_settlement_waits,
    check_outcomes, check_history, check_archive_settled, check_performance, check_book_cash, check_book_positions,
    check_book_fills, check_learning_returns, check_prices, check_option_quotes, check_learning_report,
    check_learner_features, check_learner_labels)


def run(src: Sources, *, budget: float | None = None,
        progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Every check, in order; with a time ``budget`` (seconds) the checks left when it runs out are reported as not
    run rather than slowing the caller. ``progress`` hears each check's name as it starts (the CLI prints it, so a
    long check on a large desk is visibly working)."""
    started = time.monotonic()
    findings = []
    for i, check in enumerate(CHECKS, 1):
        name = check.__name__.removeprefix("check_")
        if progress is not None:
            progress(f"[{i}/{len(CHECKS)}] {name.replace('_', ' ')} ({time.monotonic() - started:.0f}s)")
        if budget is not None and time.monotonic() - started > budget:
            findings.append(Finding(name, "", check.__doc__ or name, skipped="time budget used up"))
            continue
        try:
            findings.append(check(src))
        except (sqlite3.Error, KeyError, ValueError, TypeError) as exc:
            # A check that cannot read its data says so; it never hides the others.
            found = Finding(name, "", name, skipped=f"could not run: {type(exc).__name__}")
            findings.append(found)
    return {"version": VERSION, "checked_at": src.now.isoformat(),
            "seconds": round(time.monotonic() - started, 2),
            "checks": len(findings), "violations": sum(f.violations for f in findings),
            "findings": [f.as_json() for f in findings]}


def check(paths: DeskPaths, now: datetime | None = None, *, budget: float | None = None,
          history_sessions: int = HISTORY_SESSIONS, progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    with open_sources(paths, now, history_sessions=history_sessions) as src:
        return run(src, budget=budget, progress=progress)


def summary(report: Mapping[str, Any]) -> dict[str, Any]:
    """Counts only (for the Data health card)."""
    return {"version": report["version"], "checked_at": report["checked_at"], "violations": report["violations"],
            "checks": [{k: f[k] for k in ("id", "area", "title", "checked", "violations", "skipped")}
                       for f in report["findings"]]}


def text(report: Mapping[str, Any]) -> str:
    """The plain report ``desk stocks check-accuracy`` prints."""
    lines = [f"KESTREL accuracy check · {report['checked_at'][:19]} · {report['checks']} checks · "
             f"{report['violations']} violation{'s' if report['violations'] != 1 else ''} · {report['seconds']}s", ""]
    for f in report["findings"]:
        mark = "SKIP" if f["skipped"] else "OK  " if not f["violations"] else "FAIL"
        detail = (f"not run: {f['skipped']}" if f["skipped"] else
                  f"{f['violations']} of {f['checked']:,}" if f["violations"] else f"{f['checked']:,} checked")
        lines.append(f"{mark} {f['id']:<26} {detail}")
        if f["violations"] or f["skipped"]:
            lines.append(f"     {f['title']}")
        lines.extend(f"     · {e}" for e in f["examples"])
        lines.extend(f"     note: {n}" for n in f.get("notes") or [])
    lines += ["", "Read-only: nothing was changed. Examples are at most five per check; counts are complete."]
    return "\n".join(lines)


__all__ = ["CHECKS", "Finding", "Recorded", "Sources", "board_plays", "breakeven_problems", "chance_order", "check",
           "open_sources", "performance_problems", "position_problems", "probabilities", "recorded", "report_problems",
           "risk_problems", "row_problems", "run", "settle_value", "stats_problems", "summary", "summary_problems",
           "text", "worst_loss"]
