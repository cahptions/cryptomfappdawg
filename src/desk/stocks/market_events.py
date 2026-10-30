"""Notable market events: a dated log of unusual days, found by fixed, versioned rules from stored end-of-day prices,
VIX, the 10-year Treasury yield and high-yield credit spreads.

Only the numbers in this module decide what is notable, never headlines or a model, and each session is judged
from data through its own close: the 63 sessions before it set a usual move, and liquidity ranks come from the
sessions before it. The log is context for reading results. It is not a research input and not a trade signal.

A row is written once per (rules, day, kind, subject) and never rewritten. Changing a rule needs a new ``VERSION``;
the new rules then go back over stored history under their own fingerprint, and earlier rows are kept. Headlines are
fetched afterwards for recent big single-stock moves, for reading only.
"""

from __future__ import annotations

import json
import math
import sqlite3
import warnings
from bisect import bisect_right
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

import numpy as np

from desk.core.clock import to_iso
from desk.core.errors import DeskError
from desk.core.hashing import canonical_json, sha256_hex
from desk.stocks import calendar as cal
from desk.stocks.contracts import RatePoint
from desk.stocks.prices import split_factors
from desk.stocks.repo import StocksMarket, date_of, day_of
from desk.stocks.universe import BENCHMARKS
from desk.storage.db import transaction

VERSION = 1
NAME = "stocks_market_events"
USUAL = 63                  # sessions of daily returns that set the usual move
MIN_USUAL = 40
YEAR = 252                  # sessions in the 52-week window
RECORD_HISTORY = 250        # returns needed before a move can be called the largest in stored prices
RUN_SESSIONS = 5
MOST_TRADED = 100           # single stocks: only the most traded US stocks (median dollar volume, prior 20 sessions)
BASKET_COVERAGE = 0.6       # a basket's day counts when this share of its members traded
HEADLINE_DAYS = 30
HEADLINES_PER_PASS = 2
SPLIT_WAIT = timedelta(days=2)

INDEXES = {"SPY": "S&P 500", "QQQ": "Nasdaq 100", "IWM": "Russell 2000", "DIA": "Dow Jones"}
GROUPS = {"XLK": "Technology", "XLF": "Financials", "XLV": "Health care", "XLE": "Energy", "XLI": "Industrials",
          "XLY": "Consumer discretionary", "XLP": "Consumer staples", "XLU": "Utilities", "XLB": "Materials",
          "XLRE": "Real estate", "XLC": "Communication services", "TLT": "Long-term Treasury bonds", "GLD": "Gold"}


@dataclass(frozen=True)
class Basket:
    id: str
    label: str
    members: tuple[str, ...]
    note: str


BASKETS = (
    Basket("ai", "AI stocks", ("NVDA", "AMD", "AVGO", "TSM", "MU", "ARM", "MRVL", "ASML", "SMCI", "DELL", "ANET", "VRT",
                              "ORCL", "MSFT", "GOOGL", "META", "AMZN", "PLTR", "CRWV"),
           "Chip makers, data-centre suppliers and the largest AI platforms, equally weighted. Members were chosen in "
           "October 2026, so earlier history is measured with today's members."),
    Basket("mag7", "Magnificent Seven", ("AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA"),
           "The seven largest US technology companies, equally weighted."),
)
THEMES = {"SMH": "Semiconductors"} | {b.id: b.label for b in BASKETS}


@dataclass(frozen=True)
class Move:
    z: float        # at least this many times the usual move
    floor: float    # and at least this large
    big: float      # severity 2 from here, 3 from twice this


DAY = {"index": Move(2.5, 0.015, 0.03), "group": Move(3.0, 0.02, 0.04), "theme": Move(2.5, 0.025, 0.05),
       "stock": Move(3.5, 0.07, 0.10)}
RUN = {"index": Move(3.0, 0.04, 0.08), "theme": Move(3.0, 0.07, 0.12)}
BREADTH = {"share": 0.85, "major": 0.92, "extreme": 0.97, "min_stocks": 300, "min_price": 5.0,
           "min_dollar_volume": 20e6}
DRAWDOWNS = ((0.20, "a bear market", 3), (0.10, "a correction", 2))
DRAWDOWN_SUBJECTS = ("SPY", "QQQ", "SMH", "ai")
TREND = {"average": 200, "held": 20}
TREND_SUBJECTS = ("SPY", "QQQ")
VIX_LEVELS = ((40.0, 3), (30.0, 2))
VIX_JUMP = {"jump": 0.30, "floor": 20.0, "major": 0.50}
RATES = {"UST10Y": ("10-year Treasury yield", 0.15, 0.25), "BAMLH0A0HYM2": ("High-yield credit spread", 0.25, 0.50)}
RATE_SERIES = ("VIXCLS", *RATES)

FAMILIES = {"index": "Market", "group": "Sectors and assets", "theme": "Themes", "stock": "Stocks",
            "market": "Market", "rates": "Volatility and rates"}
KINDS = {"move": "Big one-day move", "run": "Multi-day move", "breadth": "Broad market day",
         "drawdown": "Fall from a high", "recovery": "Recovery to a high", "trend": "Long-term trend break",
         "volatility": "Volatility", "rates": "Rates and credit"}


def content() -> dict[str, Any]:
    """Everything that decides what is logged; its hash is the rules' fingerprint."""
    return {"version": VERSION, "usual_sessions": USUAL, "min_usual": MIN_USUAL, "year_sessions": YEAR,
            "record_history": RECORD_HISTORY, "run_sessions": RUN_SESSIONS, "most_traded": MOST_TRADED,
            "basket_coverage": BASKET_COVERAGE, "indexes": INDEXES, "groups": GROUPS, "themes": THEMES,
            "baskets": [{"id": b.id, "members": list(b.members)} for b in BASKETS],
            "day": {k: vars(v) for k, v in DAY.items()}, "run": {k: vars(v) for k, v in RUN.items()},
            "breadth": BREADTH, "drawdowns": [list(d) for d in DRAWDOWNS], "drawdown_subjects": list(DRAWDOWN_SUBJECTS),
            "trend": TREND, "trend_subjects": list(TREND_SUBJECTS), "vix_levels": [list(v) for v in VIX_LEVELS],
            "vix_jump": VIX_JUMP, "rates": {k: list(v) for k, v in RATES.items()}}


SPEC_SHA256 = sha256_hex(canonical_json(content()))


def rules() -> list[dict[str, str]]:
    """The rules in words, for the events page."""
    def pct(x: float) -> str:
        return f"{x * 100:g}%"
    d, r = DAY, RUN
    return [
        {"kind": "move", "text": f"An index (S&P 500, Nasdaq 100, Russell 2000, Dow) moves at least {pct(d['index'].floor)} "
                                 f"and {d['index'].z:g}× its usual daily move (the typical move over the prior {USUAL} "
                                 f"sessions); a sector fund, Treasuries or gold at least {pct(d['group'].floor)} and "
                                 f"{d['group'].z:g}×; semiconductors (SMH) or a theme basket at least "
                                 f"{pct(d['theme'].floor)} and {d['theme'].z:g}×."},
        {"kind": "move", "text": f"One of the {MOST_TRADED} most traded US stocks moves at least {pct(d['stock'].floor)} "
                                 f"and {d['stock'].z:g}× its own usual move. Its largest move in stored prices "
                                 f"(at least {RECORD_HISTORY} sessions) and a results filing (SEC 8-K item 2.02) that "
                                 "day are noted."},
        {"kind": "run", "text": f"Over {RUN_SESSIONS} sessions an index moves at least {pct(r['index'].floor)}, or "
                                f"semiconductors or a theme at least {pct(r['theme'].floor)}, and {r['index'].z:g}× the "
                                f"usual {RUN_SESSIONS}-session move; logged the first day it qualifies."},
        {"kind": "breadth", "text": f"At least {pct(BREADTH['share'])} of actively traded US stocks (above "
                                    f"${BREADTH['min_price']:g}, over ${BREADTH['min_dollar_volume'] / 1e6:g}M a day) "
                                    "fall, or rise, on the same day."},
        {"kind": "drawdown", "text": "The S&P 500, Nasdaq 100, semiconductors or AI stocks first close 10% (a "
                                     "correction) or 20% (a bear market) below their 52-week high, and when they "
                                     "get back to a 52-week high after a fall of at least 10%."},
        {"kind": "trend", "text": f"The S&P 500 or Nasdaq 100 closes on the other side of its {TREND['average']}-day "
                                  f"average after at least {TREND['held']} sessions on one side."},
        {"kind": "volatility", "text": f"VIX closes above {VIX_LEVELS[1][0]:g} or {VIX_LEVELS[0][0]:g}, or jumps at least "
                                       f"{pct(VIX_JUMP['jump'])} in a day to {VIX_JUMP['floor']:g} or more."},
        {"kind": "rates", "text": f"The 10-year Treasury yield moves at least {RATES['UST10Y'][1]:g} percentage points in "
                                  f"a day, or the high-yield credit spread at least {RATES['BAMLH0A0HYM2'][1]:g}."},
    ]


@dataclass(frozen=True)
class Event:
    day: date
    kind: str           # see KINDS
    family: str         # see FAMILIES
    subject: str        # a symbol, a basket id, "market" or a rate series
    severity: int       # 1 notable, 2 major, 3 extreme
    direction: str      # up | down: the price or level itself
    tone: str           # negative | positive | neutral: what it usually means for stock holders
    title: str
    summary: str
    detail: dict[str, Any]


@dataclass
class Closes:
    """Split-adjusted closes and volumes, symbols × sessions (NaN where no bar)."""
    symbols: list[str]
    days: list[date]
    close: np.ndarray
    volume: np.ndarray
    names: dict[str, str]
    stocks: np.ndarray      # True for research stocks (not ETFs)

    def __post_init__(self) -> None:
        self.index = {s: i for i, s in enumerate(self.symbols)}
        with np.errstate(divide="ignore", invalid="ignore"):
            self.returns = np.full(self.close.shape, np.nan)
            self.returns[:, 1:] = self.close[:, 1:] / self.close[:, :-1] - 1
        self._baskets: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self._ranks: dict[int, tuple[np.ndarray, np.ndarray]] = {}

    def series(self, subject: str) -> tuple[np.ndarray, np.ndarray] | None:
        """(levels, daily returns) for a symbol or a basket id."""
        basket = next((b for b in BASKETS if b.id == subject), None)
        if basket is None:
            i = self.index.get(subject)
            return None if i is None else (self.close[i], self.returns[i])
        if subject not in self._baskets:
            rows = [self.index[m] for m in basket.members if m in self.index]
            if not rows:
                return None
            daily = self.returns[rows]
            finite = np.isfinite(daily)
            counted = finite.sum(axis=0)
            with np.errstate(invalid="ignore", divide="ignore"):
                mean = np.where(finite, daily, 0.0).sum(axis=0) / counted
            mean[counted < BASKET_COVERAGE * len(basket.members)] = np.nan
            # An equal-weight index rebalanced daily; a day without enough members carries the level unchanged.
            self._baskets[subject] = (np.cumprod(1 + np.nan_to_num(mean)), mean)
        return self._baskets[subject]


def label(subject: str) -> str:
    return INDEXES.get(subject) or GROUPS.get(subject) or THEMES.get(subject) or subject


def _usual(returns: np.ndarray, end: int) -> float | None:
    """The usual daily move: standard deviation of the returns in the USUAL sessions before ``end``."""
    window = returns[max(0, end - USUAL):end]
    window = window[np.isfinite(window)]
    return float(np.std(window, ddof=1)) if window.size >= MIN_USUAL else None


def _severity(move: float, z: float, rule: Move) -> int:
    size = abs(move)
    return 3 if size >= 2 * rule.big else 2 if size >= rule.big or abs(z) >= 2 * rule.z else 1


def _pct(x: float, sign: bool = False) -> str:
    text = f"{abs(x) * 100:.1f}%"
    return (("+" if x > 0 else "−") + text) if sign else text


def _verb(move: float) -> str:
    return "rose" if move > 0 else "fell"


def _when(d: date) -> str:
    return f"{d:%b} {d.day}, {d.year}"


def _dollar_rank(c: Closes, j: int) -> tuple[np.ndarray, np.ndarray]:
    """Each stock's median dollar volume over the 20 sessions before ``j`` and its rank (0 = most traded)."""
    if j not in c._ranks:
        if j < 1:
            dollars = np.full(len(c.symbols), np.nan)
        else:
            window = slice(max(0, j - 20), j)
            with warnings.catch_warnings(), np.errstate(invalid="ignore"):
                warnings.simplefilter("ignore", RuntimeWarning)       # a symbol with no bars in the window
                dollars = np.nanmedian(c.close[:, window] * c.volume[:, window], axis=1)
            dollars = np.where(c.stocks, dollars, np.nan)
        order = np.argsort(np.where(np.isfinite(dollars), -dollars, np.inf), kind="stable")
        rank = np.full(len(c.symbols), np.inf)
        finite = np.isfinite(dollars[order])
        rank[order[finite]] = np.arange(int(finite.sum()))
        c._ranks = {j: (dollars, rank)}
    return c._ranks[j]


def _subject_moves(c: Closes, j: int) -> list[Event]:
    out: list[Event] = []
    day = c.days[j]
    for family, names in (("index", INDEXES), ("group", GROUPS), ("theme", THEMES)):
        rule = DAY[family]
        for subject in names:
            got = c.series(subject)
            if got is None:
                continue
            level, returns = got
            move, usual = returns[j], _usual(returns, j)
            if not np.isfinite(move) or not usual:
                continue
            z = float(move / usual)
            if abs(move) < rule.floor or abs(z) < rule.z:
                continue
            detail: dict[str, Any] = {"move": float(move), "z": z, "usual": usual}
            text = f"{abs(z):.1f}× its usual daily move of {_pct(usual)} (prior {USUAL} sessions)."
            if subject in INDEXES or subject in GROUPS or subject == "SMH":
                detail |= {"symbol": subject, "close": float(level[j])}
            else:
                detail |= _members(c, subject, j, move)
                text = f"{detail['moved']} of {detail['counted']} members {_verb(move)}. " + text
            out.append(Event(day, "move", family, subject, _severity(move, z, rule), "up" if move > 0 else "down",
                             _tone(subject, move), f"{label(subject)} {_verb(move)} {_pct(move)}", text, detail))
    return out


def _tone(subject: str, move: float) -> str:
    if subject in ("TLT", "GLD"):
        return "neutral"
    return "positive" if move > 0 else "negative"


def _members(c: Closes, subject: str, j: int, move: float, start: int | None = None) -> dict[str, Any]:
    basket = next(b for b in BASKETS if b.id == subject)
    moves: list[tuple[str, float]] = []
    for m in basket.members:
        i = c.index.get(m)
        if i is None:
            continue
        a, b = c.close[i, j - 1 if start is None else start], c.close[i, j]
        if np.isfinite(a) and np.isfinite(b) and a > 0:
            moves.append((m, float(b / a - 1)))
    moves.sort(key=lambda r: r[1], reverse=move > 0)
    return {"members": [{"symbol": m, "move": x} for m, x in moves], "counted": len(moves),
            "moved": sum(1 for _, x in moves if x * move > 0), "basket": basket.note}


def _runs(c: Closes, j: int) -> list[Event]:
    out: list[Event] = []

    def qualifies(returns: np.ndarray, level: np.ndarray, k: int, rule: Move) -> tuple[float, float, float] | None:
        if k - RUN_SESSIONS < 0:
            return None
        start = k - RUN_SESSIONS
        a, b = level[start], level[k]
        usual = _usual(returns, start + 1)
        if not (np.isfinite(a) and np.isfinite(b)) or a <= 0 or not usual \
                or not np.isfinite(returns[start + 1:k + 1]).all():
            return None
        move = float(b / a - 1)
        z = move / (usual * math.sqrt(RUN_SESSIONS))
        return (move, z, usual) if abs(move) >= rule.floor and abs(z) >= rule.z else None

    for family, names in (("index", INDEXES), ("theme", THEMES)):
        rule = RUN[family]
        for subject in names:
            got = c.series(subject)
            if got is None:
                continue
            level, returns = got
            found = qualifies(returns, level, j, rule)
            if found is None or any(qualifies(returns, level, j - k, rule) for k in range(1, RUN_SESSIONS + 1)):
                continue
            move, z, usual = found
            first = c.days[j - RUN_SESSIONS + 1]
            detail: dict[str, Any] = {"move": move, "z": z, "usual": usual, "sessions": RUN_SESSIONS,
                                      "from": first.isoformat()}
            if subject in INDEXES or subject == "SMH":
                detail["symbol"] = subject
            else:
                detail |= _members(c, subject, j, move, start=j - RUN_SESSIONS)
            out.append(Event(c.days[j], "run", family, subject, _severity(move, z, rule), "up" if move > 0 else "down",
                             _tone(subject, move),
                             f"{label(subject)} {_verb(move)} {_pct(move)} over {RUN_SESSIONS} sessions",
                             f"{first:%b} {first.day} to {c.days[j]:%b} {c.days[j].day}: {abs(z):.1f}× the usual "
                             f"{RUN_SESSIONS}-session move.", detail))
    return out


def _stock_moves(c: Closes, j: int, reports: Callable[[str], dict[date, date]]) -> list[Event]:
    rule, out = DAY["stock"], []
    _, rank = _dollar_rank(c, j)
    first_day = c.days[0]
    for i in np.flatnonzero(rank < MOST_TRADED):
        symbol, returns = c.symbols[i], c.returns[i]
        move, usual = returns[j], _usual(returns, j)
        if not np.isfinite(move) or not usual or abs(move) < rule.floor:
            continue
        z = float(move / usual)
        if abs(z) < rule.z:
            continue
        severity = _severity(move, z, rule)
        name = c.names.get(symbol) or symbol
        detail: dict[str, Any] = {"symbol": symbol, "move": float(move), "z": z, "usual": usual,
                                  "close": float(c.close[i, j]), "most_traded_rank": int(rank[i]) + 1}
        parts = [f"#{int(rank[i]) + 1} most traded US stock; {abs(z):.1f}× its usual daily move of {_pct(usual)}."]
        prior = returns[:j][np.isfinite(returns[:j])]
        if prior.size >= RECORD_HISTORY and (move < prior.min() if move < 0 else move > prior.max()):
            detail["record_since"] = first_day.isoformat()
            parts.append(f"Its largest one-day {'fall' if move < 0 else 'rise'} in stored prices "
                         f"(since {_when(first_day)}).")
            severity = min(3, severity + 1)
        filed = reports(symbol).get(c.days[j])
        if filed is not None:
            detail["results_filed"] = filed.isoformat()
            parts.append(f"The day it reported results (SEC 8-K filed {filed:%b} {filed.day}).")
        out.append(Event(c.days[j], "move", "stock", symbol, severity, "up" if move > 0 else "down",
                         _tone(symbol, move), f"{name if name == symbol else f'{name} ({symbol})'} {_verb(move)} "
                         f"{_pct(move)}", " ".join(parts), detail))
    return out


def _breadth(c: Closes, j: int) -> list[Event]:
    if j < 1:
        return []
    dollars, _ = _dollar_rank(c, j)
    moves = c.returns[:, j]
    with np.errstate(invalid="ignore"):
        active = (c.stocks & np.isfinite(moves) & (c.close[:, j - 1] >= BREADTH["min_price"])
                  & (dollars >= BREADTH["min_dollar_volume"]))
    counted = int(active.sum())
    if counted < BREADTH["min_stocks"]:
        return []
    fell, rose = int((moves[active] < 0).sum()), int((moves[active] > 0).sum())
    median = float(np.median(moves[active]))
    out = []
    for share, word, direction in ((fell / counted, "sell-off", "down"), (rose / counted, "rally", "up")):
        if share < BREADTH["share"]:
            continue
        severity = 3 if share >= BREADTH["extreme"] else 2 if share >= BREADTH["major"] else 1
        out.append(Event(c.days[j], "breadth", "market", "market", severity, direction,
                         "negative" if direction == "down" else "positive",
                         f"Broad {word}: {share * 100:.0f}% of {counted:,} actively traded stocks "
                         f"{'fell' if direction == 'down' else 'rose'}",
                         f"Stocks above ${BREADTH['min_price']:g} trading over "
                         f"${BREADTH['min_dollar_volume'] / 1e6:g}M a day; the median stock {_verb(median)} "
                         f"{_pct(median)}.",
                         {"share": share, "counted": counted, "fell": fell, "rose": rose, "median_move": median}))
    return out


def _drawdowns(c: Closes, j: int) -> list[Event]:
    out: list[Event] = []
    if j < YEAR - 1:
        return out
    for subject in DRAWDOWN_SUBJECTS:
        got = c.series(subject)
        if got is None:
            continue
        level = got[0]
        window = level[j - YEAR + 1:j + 1]
        if not np.isfinite(level[j]) or np.isfinite(window).sum() < YEAR * 0.9:
            continue
        peak_at = j - YEAR + 1 + int(np.nanargmax(window))
        peak = float(level[peak_at])
        if peak_at < j:
            fall = float(level[j] / peak - 1)
            for size, words, severity in DRAWDOWNS:
                before = level[peak_at + 1:j]
                if fall <= -size and not (before[np.isfinite(before)] <= peak * (1 - size)).any():
                    high = c.days[peak_at]
                    out.append(Event(c.days[j], "drawdown", "theme" if subject in THEMES else "index", subject,
                                     severity, "down", "negative",
                                     f"{label(subject)} entered {words}: {_pct(fall)} below its high",
                                     f"Its 52-week high was {_when(high)}; this is the first close {size * 100:g}% "
                                     "or more below it.",
                                     {"symbol": subject if subject not in THEMES or subject == "SMH" else None,
                                      "fall": fall, "high_day": high.isoformat(), "threshold": size}))
                    break
            continue
        prior = level[j - YEAR + 1:j]
        if not np.isfinite(prior).any():
            continue
        last_peak = j - YEAR + 1 + int(np.nanargmax(prior))
        since = level[last_peak:j]
        low_at = last_peak + int(np.nanargmin(since))
        fall = float(level[low_at] / level[last_peak] - 1)
        if fall <= -DRAWDOWNS[1][0]:
            low = c.days[low_at]
            out.append(Event(c.days[j], "recovery", "theme" if subject in THEMES else "index", subject, 1, "up",
                             "positive", f"{label(subject)} is back at a 52-week high",
                             f"It had fallen {_pct(fall)} from its previous high (low on {_when(low)}).",
                             {"symbol": subject if subject not in THEMES or subject == "SMH" else None,
                              "fall": fall, "low_day": low.isoformat()}))
    return out


def _trends(c: Closes, j: int) -> list[Event]:
    out: list[Event] = []
    n, held = TREND["average"], TREND["held"]
    if j < n + held:
        return out
    for subject in TREND_SUBJECTS:
        got = c.series(subject)
        if got is None:
            continue
        level = got[0]
        sides = []
        for k in range(j - held, j + 1):
            window = level[k - n + 1:k + 1]
            if not np.isfinite(window).all():
                break
            sides.append(bool(level[k] > window.mean()))
        if len(sides) != held + 1 or sides[-1] == sides[-2] or len(set(sides[:-1])) != 1:
            continue
        above = sides[-1]
        average = float(level[j - n + 1:j + 1].mean())
        out.append(Event(c.days[j], "trend", "index", subject, 1, "up" if above else "down",
                         "positive" if above else "negative",
                         f"{label(subject)} closed {'above' if above else 'below'} its {n}-day average",
                         f"After at least {held} sessions {'below' if above else 'above'} it; the {n}-day average "
                         f"is {average:,.2f} ({subject}).",
                         {"symbol": subject, "average": average, "close": float(level[j])}))
    return out


def scan_prices(c: Closes, j: int, reports: Callable[[str], dict[date, date]] = lambda s: {}) -> list[Event]:
    """Every price event on session ``j``, from closes through ``j`` only."""
    return [*_subject_moves(c, j), *_runs(c, j), *_stock_moves(c, j, reports), *_breadth(c, j), *_drawdowns(c, j),
            *_trends(c, j)]


def scan_rates(points: dict[str, list[RatePoint]], day: date) -> list[Event]:
    """VIX, yield and credit-spread events on ``day``, from observations through ``day`` only."""
    out: list[Event] = []

    def pair(series: str) -> tuple[float, float] | None:
        rows = points.get(series, [])
        k = bisect_right([p.day for p in rows], day)
        if k < 2 or rows[k - 1].day != day:
            return None
        return rows[k - 2].value, rows[k - 1].value

    vix = pair("VIXCLS")
    if vix is not None:
        before, now = vix
        crossed = next(((level, sev) for level, sev in VIX_LEVELS if before < level <= now), None)
        jump = now / before - 1 if before > 0 else 0.0
        detail = {"value": now, "previous": before, "change": jump}
        if crossed is not None:
            out.append(Event(day, "volatility", "rates", "VIX", crossed[1], "up", "negative",
                             f"VIX closed above {crossed[0]:g} ({now:.1f})",
                             f"Up from {before:.1f}: options are pricing large daily swings in the S&P 500.", detail))
        elif jump >= VIX_JUMP["jump"] and now >= VIX_JUMP["floor"]:
            out.append(Event(day, "volatility", "rates", "VIX", 2 if jump >= VIX_JUMP["major"] else 1, "up", "negative",
                             f"VIX jumped {_pct(jump)} to {now:.1f}", f"From {before:.1f} the session before.", detail))
    for series, (name, step, major) in RATES.items():
        got = pair(series)
        if got is None:
            continue
        before, now = got
        change = now - before
        if abs(change) < step:
            continue
        credit = series == "BAMLH0A0HYM2"
        verb = ("widened" if change > 0 else "narrowed") if credit else ("rose" if change > 0 else "fell")
        meaning = (" Lenders want more to hold risky debt." if change > 0 else
                   " Lenders are more willing to hold risky debt.") if credit else ""
        out.append(Event(day, "rates", "rates", series, 2 if abs(change) >= major else 1,
                         "up" if change > 0 else "down",
                         ("negative" if change > 0 else "positive") if credit else "neutral",
                         f"{name} {verb} {abs(change):.2f} points to {now:.2f}%",
                         f"From {before:.2f}% the session before.{meaning}",
                         {"value": now, "previous": before, "change": change}))
    return out


# ── storage ──────────────────────────────────────────────────────────────────────────────────────
def save(conn: sqlite3.Connection, events: Iterable[Event], now: datetime, basis: str) -> int:
    rows = [(SPEC_SHA256, day_of(e.day), e.kind, e.subject, e.family, e.severity, e.direction, e.tone, e.title,
             e.summary, json.dumps(e.detail, allow_nan=False, sort_keys=True), to_iso(now), basis) for e in events]
    before = conn.total_changes
    conn.executemany("INSERT OR IGNORE INTO stk_market_events(spec_sha256, day, kind, subject, family, severity, "
                     "direction, tone, title, summary, detail_json, detected_at, basis) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return conn.total_changes - before


def _scanned(conn: sqlite3.Connection, source: str) -> set[date]:
    return {date_of(r[0]) for r in conn.execute("SELECT day FROM stk_market_event_scans WHERE spec_sha256=? AND source=?",
                                                (SPEC_SHA256, source))}


def _mark(conn: sqlite3.Connection, source: str, days: Iterable[date], now: datetime) -> None:
    conn.executemany("INSERT OR IGNORE INTO stk_market_event_scans(spec_sha256, source, day, scanned_at) "
                     "VALUES (?,?,?,?)", [(SPEC_SHA256, source, day_of(d), to_iso(now)) for d in days])


def _put_state(conn: sqlite3.Connection, key: str, value: Any, now: datetime) -> None:
    conn.execute("INSERT INTO stk_market_event_state(key, value_json, updated_at) VALUES (?,?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                 (key, json.dumps(value, allow_nan=False), to_iso(now)))


def state(conn: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT value_json, updated_at FROM stk_market_event_state WHERE key=?", (key,)).fetchone()
    if row is None:
        return None
    try:
        value = json.loads(row[0])
    except ValueError:
        return None
    return {"value": value, "updated_at": row[1]} if isinstance(value, (dict, list)) else None


def load(market: StocksMarket, since: date | None = None, until: date | None = None) -> Closes:
    """Stored closes and volumes for every symbol (split-adjusted). Opens only two arrays, not the full OHLC panel."""
    days = [d for d in market.bar_days() if (since is None or d >= since) and (until is None or d <= until)]
    col = {day_of(d): k for k, d in enumerate(days)}
    rows: dict[str, list[tuple[int, float, float]]] = {}
    if days:
        for symbol, day, close, volume in market.conn.execute(
                "SELECT symbol, day, close, volume FROM stk_bars WHERE day >= ? AND day <= ?",
                (day_of(days[0]), day_of(days[-1]))):
            rows.setdefault(symbol, []).append((day, close, volume))
    symbols = sorted(rows)
    close, volume = np.full((len(symbols), len(days)), np.nan), np.full((len(symbols), len(days)), np.nan)
    for i, symbol in enumerate(symbols):
        for day, c, v in rows[symbol]:
            k = col[day]
            close[i, k], volume[i, k] = c, v
    if symbols and days:
        factors = split_factors(market.splits(), symbols, days)
        close, volume = close / factors, volume * factors
    listings = {r["symbol"]: r for r in market.listings(active=False)}
    funds = set(BENCHMARKS) | {s for s, r in listings.items() if r["etf"]}
    stocks = np.array([s not in funds and listings.get(s) is not None for s in symbols], dtype=bool)
    names = {s: _short_name(r["name"]) for s, r in listings.items() if r["name"]}
    return Closes(symbols, days, close, volume, names, stocks)


def _short_name(name: str) -> str:
    """'Alphabet Inc. Class A Common Stock' → 'Alphabet'."""
    words = name.replace(",", " ").split()
    stops = {"-", "inc", "inc.", "corp", "corp.", "corporation", "co", "co.", "ltd", "ltd.", "plc", "class", "common",
             "holding", "holdings", "group", "company", "sa", "s.a.", "n.v.", "nv", "ag", "se", "limited"}
    kept: list[str] = []
    for word in words:
        if word.lower() in stops:
            break
        kept.append(word)
    return " ".join(kept) or name


def _reports(market: StocksMarket) -> Callable[[str], dict[date, date]]:
    """symbol → {session that first reflected a results 8-K: filing date}."""
    from desk.stocks.signals import _session_after
    from desk.stocks.universe import symbol_ciks

    ciks = symbol_ciks(market)
    cache: dict[str, dict[date, date]] = {}

    def get(symbol: str) -> dict[date, date]:
        if symbol not in cache:
            out: dict[date, date] = {}
            cik = ciks.get(symbol)
            if cik is not None:
                for r in market.filings(cik, forms=("8-K", "8-K/A")):
                    if "2.02" in (r["items"] or "").split(","):
                        accepted = datetime.fromisoformat(r["accepted_at"]) if r["accepted_at"] else None
                        out.setdefault(_session_after(accepted, date_of(r["filed"])), date_of(r["filed"]))
            cache[symbol] = out
        return cache[symbol]
    return get


def standing(c: Closes) -> list[dict[str, Any]]:
    """Where each watched index and theme stands against its 52-week high on the newest stored session."""
    out = []
    for subject in DRAWDOWN_SUBJECTS:
        got = c.series(subject)
        if got is None or not c.days:
            continue
        level = got[0]
        j = len(c.days) - 1
        while j >= 0 and not np.isfinite(level[j]):
            j -= 1
        start = max(0, j - YEAR + 1)
        if j < 0 or not np.isfinite(level[start:j + 1]).any():
            continue
        peak_at = start + int(np.nanargmax(level[start:j + 1]))
        fall = float(level[j] / level[peak_at] - 1)
        state_words = next((words.removeprefix("a ") for size, words, _ in DRAWDOWNS if fall <= -size), None)
        out.append({"subject": subject, "label": label(subject),
                    "symbol": subject if subject not in THEMES or subject == "SMH" else None,
                    "day": c.days[j].isoformat(), "from_high": fall, "high_day": c.days[peak_at].isoformat(),
                    "sessions": j - start + 1, "full_year": j - start + 1 >= YEAR,
                    "state": state_words or ("at its high" if fall >= -0.005 else "pullback" if fall <= -0.05
                                             else "near its high")})
    return out


def _splits_checked(market: StocksMarket, day: date, now: datetime) -> bool:
    """Judge a session once splits were refreshed on or after it (so a split is never read as a crash)."""
    through = market.jobs().get("corporate_actions", {}).get("detail", {}).get("through")
    return (through is not None and date.fromisoformat(through) >= day) or now - cal.session_close(day) >= SPLIT_WAIT


def _rates_ready(points: dict[str, list[RatePoint]], day: date, now: datetime) -> bool:
    """FRED publishes the next morning: a day is judged once every series has an observation on or after it."""
    if now.date() - day > timedelta(days=7):
        return True
    return all(rows[-1].day >= day for rows in points.values() if rows)


def scan(market: StocksMarket, research: sqlite3.Connection, now: datetime, *,
         guard: Callable[[], None] = lambda: None) -> dict[str, Any]:
    """Log every stored session and rate day not yet judged under the current rules. The first run goes back over
    all stored history (basis ``history``); later sessions are logged as they arrive (basis ``close``)."""
    market.register_spec(NAME, VERSION, content(), now)
    report: dict[str, Any] = {"spec_sha256": SPEC_SHA256, "price_sessions": 0, "rate_days": 0, "logged": 0}
    days = market.bar_days()
    done = _scanned(research, "prices")
    pending = [d for d in days if d not in done]
    ready = [d for d in pending if _splits_checked(market, d, now)]
    held = [d for d in pending if d not in ready]
    if held:
        report["waiting"] = f"{held[0].isoformat()} waits for split data refreshed after that session"
    if ready:
        guard()
        closes = load(market)
        reports = _reports(market)
        position = {d: k for k, d in enumerate(closes.days)}
        for d in ready:
            k = position.get(d)
            if k is None:
                continue
            basis = "close" if now - cal.session_close(d) <= timedelta(days=1) else "history"
            found = scan_prices(closes, k, reports)
            with transaction(research):
                report["logged"] += save(research, found, now, basis)
                _mark(research, "prices", [d], now)
            report["price_sessions"] += 1
        with transaction(research):
            _put_state(research, "standing", standing(closes), now)
    points = {s: market.rates(s) for s in RATE_SERIES}
    known = sorted({p.day for rows in points.values() for p in rows})
    if known:
        done = _scanned(research, "rates")
        rate_days = [d for d in cal.trading_days(known[0], min(known[-1], now.date()))
                     if d not in done and _rates_ready(points, d, now)]
        with transaction(research):
            for d in rate_days:
                basis = "close" if now - cal.session_close(d) <= timedelta(days=2) else "history"
                report["logged"] += save(research, scan_rates(points, d), now, basis)
            _mark(research, "rates", rate_days, now)
        report["rate_days"] = len(rate_days)
    with transaction(research):
        _put_state(research, "scan", {**report, "finished_at": to_iso(now),
                                      "first_day": days[0].isoformat() if days else None,
                                      "last_day": days[-1].isoformat() if days else None}, now)
    return report


# ── headlines (reading only; fetched after the event is logged) ─────────────────────────────────
def headline_window(day: date) -> tuple[datetime, datetime]:
    """From the previous session's close to the end of the event day, New York time."""
    start = cal.session_close(cal.previous_trading_day(day))
    return start, datetime.combine(day, time(23, 59), start.tzinfo)


def headlines_due(research: sqlite3.Connection, now: datetime, limit: int = HEADLINES_PER_PASS) -> list[tuple[date, str]]:
    since = day_of(now.date() - timedelta(days=HEADLINE_DAYS))
    rows = research.execute(
        "SELECT DISTINCT e.day, e.subject FROM stk_market_events e LEFT JOIN stk_market_event_news n "
        "ON n.day = e.day AND n.subject = e.subject WHERE e.family = 'stock' AND e.day >= ? AND e.spec_sha256 = ? "
        "AND (n.subject IS NULL OR (n.items_json IS NULL AND n.attempted_at < ?)) "
        "ORDER BY e.severity DESC, e.day DESC LIMIT ?",
        (since, SPEC_SHA256, to_iso(now - timedelta(hours=6)), limit)).fetchall()
    return [(date_of(r[0]), r[1]) for r in rows]


def save_headlines(research: sqlite3.Connection, day: date, subject: str, items: Sequence[dict[str, Any]] | None,
                   now: datetime, error: str | None = None) -> None:
    keep = None if items is None else [{k: i.get(k) for k in ("title", "url", "source", "published")}
                                       for i in items[:5]]
    with transaction(research):
        research.execute(
            "INSERT INTO stk_market_event_news(day, subject, attempted_at, fetched_at, items_json, error) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(day, subject) DO UPDATE SET attempted_at=excluded.attempted_at, "
            "fetched_at=COALESCE(excluded.fetched_at, stk_market_event_news.fetched_at), "
            "items_json=COALESCE(excluded.items_json, stk_market_event_news.items_json), error=excluded.error",
            (day_of(day), subject, to_iso(now), to_iso(now) if keep is not None else None,
             json.dumps(keep) if keep is not None else None, error))


async def fetch_headlines(massive: Any, research: sqlite3.Connection, now: datetime) -> dict[str, Any]:
    fetched, errors = 0, []
    if not getattr(massive, "configured", False):
        return {"headlines": 0}
    for day, subject in headlines_due(research, now):
        start, end = headline_window(day)
        try:
            items = await massive.news(subject, limit=5, published_after=start, published_before=end)
        except DeskError as exc:
            save_headlines(research, day, subject, None, now, str(exc)[:200])
            errors.append(f"{subject} {day.isoformat()}: {str(exc)[:120]}")
            continue
        save_headlines(research, day, subject, items, now)
        fetched += 1
    return {"headlines": fetched, **({"headline_errors": errors} if errors else {})}


async def job(suite: Any, market: StocksMarket, research: sqlite3.Connection, now: datetime, *,
              guard: Callable[[], None] = lambda: None) -> dict[str, Any]:
    """The worker's pass: judge new sessions and rate days, then fetch a few headlines. Recorded as a job."""
    from desk.stocks.collect import _job

    async def work() -> dict[str, Any]:
        report = scan(market, research, now, guard=guard)
        return report | await fetch_headlines(suite.massive, research, now)
    return await _job(market, "market_events", now, work)


# ── reading ──────────────────────────────────────────────────────────────────────────────────────
GROUPINGS = {"market": ("index", "market"), "sectors": ("group", "theme"), "stocks": ("stock",), "rates": ("rates",)}


def read(conn: sqlite3.Connection, *, group: str | None = None, severity: int = 1, symbol: str | None = None,
         since: date | None = None, until: date | None = None, limit: int = 200) -> list[dict[str, Any]]:
    sql = ("SELECT e.*, n.items_json FROM stk_market_events e LEFT JOIN stk_market_event_news n "
           "ON n.day = e.day AND n.subject = e.subject WHERE e.spec_sha256 = ? AND e.severity >= ?")
    args: list[Any] = [SPEC_SHA256, severity]
    if group in GROUPINGS:
        families = GROUPINGS[group]
        sql += f" AND e.family IN ({','.join('?' * len(families))})"
        args += families
    if symbol is not None:
        sql += " AND (e.subject = ? OR json_extract(e.detail_json, '$.symbol') = ?)"
        args += [symbol, symbol]
    if since is not None:
        sql += " AND e.day >= ?"
        args.append(day_of(since))
    if until is not None:
        sql += " AND e.day <= ?"
        args.append(day_of(until))
    sql += " ORDER BY e.day DESC, e.severity DESC, e.family, e.subject LIMIT ?"
    args.append(limit)
    out = []
    for r in conn.execute(sql, args):
        try:
            detail = json.loads(r["detail_json"])
            news = json.loads(r["items_json"]) if r["items_json"] else None
        except ValueError:
            continue
        out.append({"day": date_of(r["day"]).isoformat(), "kind": r["kind"], "kind_label": KINDS.get(r["kind"]),
                    "family": r["family"], "family_label": FAMILIES.get(r["family"]), "subject": r["subject"],
                    "label": label(r["subject"]), "severity": r["severity"], "direction": r["direction"],
                    "tone": r["tone"], "title": r["title"], "summary": r["summary"], "detail": detail,
                    "headlines": news, "detected_at": r["detected_at"], "basis": r["basis"]})
    return out


def counts(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT COUNT(*), MIN(day), MAX(day) FROM stk_market_events WHERE spec_sha256 = ?",
                       (SPEC_SHA256,)).fetchone()
    sessions = conn.execute("SELECT COUNT(*), MIN(day), MAX(day) FROM stk_market_event_scans "
                            "WHERE spec_sha256 = ? AND source = 'prices'", (SPEC_SHA256,)).fetchone()
    rates = conn.execute("SELECT MAX(day) FROM stk_market_event_scans WHERE spec_sha256 = ? AND source = 'rates'",
                         (SPEC_SHA256,)).fetchone()
    return {"events": row[0], "first_event": date_of(row[1]).isoformat() if row[1] else None,
            "last_event": date_of(row[2]).isoformat() if row[2] else None, "sessions": sessions[0],
            "first_session": date_of(sessions[1]).isoformat() if sessions[1] else None,
            "last_session": date_of(sessions[2]).isoformat() if sessions[2] else None,
            "rates_through": date_of(rates[0]).isoformat() if rates[0] else None}


def page(conn: sqlite3.Connection, **filters: Any) -> dict[str, Any]:
    stand, last = state(conn, "standing"), state(conn, "scan")
    return {"version": VERSION, "spec_sha256": SPEC_SHA256, "rules": rules(),
            "baskets": [{"id": b.id, "label": b.label, "members": list(b.members), "note": b.note} for b in BASKETS],
            "coverage": counts(conn), "scan": last["value"] if last else None,
            "standing": stand["value"] if stand else [], "standing_updated_at": stand["updated_at"] if stand else None,
            "events": read(conn, **filters),
            "basis": "Found by fixed rules from stored end-of-day prices, VIX, Treasury yields and credit spreads. "
                     "Context for reading results; not a forecast and not a trade signal."}


def empty_page() -> dict[str, Any]:
    return {"version": VERSION, "spec_sha256": SPEC_SHA256, "rules": rules(), "baskets": [], "coverage": {},
            "scan": None, "standing": [], "standing_updated_at": None, "events": [], "basis": ""}


def recent(conn: sqlite3.Connection, now: date, *, limit: int = 5) -> list[dict[str, Any]]:
    """The home page's few: major events of the last 30 days, or the latest notable ones if there were none."""
    since = now - timedelta(days=30)
    major = read(conn, severity=2, since=since, limit=limit)
    return major if len(major) >= 3 else read(conn, since=since, limit=limit)


__all__ = [
    "BASKETS",
    "GROUPS",
    "INDEXES",
    "KINDS",
    "SPEC_SHA256",
    "THEMES",
    "VERSION",
    "Closes",
    "Event",
    "content",
    "counts",
    "empty_page",
    "fetch_headlines",
    "headline_window",
    "headlines_due",
    "job",
    "load",
    "page",
    "read",
    "recent",
    "rules",
    "save",
    "save_headlines",
    "scan",
    "scan_prices",
    "scan_rates",
    "standing",
    "state",
]
