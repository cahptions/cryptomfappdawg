"""The Learner's action space: a trade described by dials, not picked from a menu (docs/STOCKS_LEARNER.md → Actions).

A trade is **structure × target |delta| × days to expiry × width**, and how it is left is one of a small **exit-rule
grid**. The structures are the defined-risk set Plays already supports (``plays.TYPES`` names): long call, long put,
call debit spread, put debit spread, cash-secured put, short put spread, short call spread, iron condor, long straddle
and long strangle. Nothing here sells an uncovered option.

* **|delta|** is the anchor leg's: the bought leg of a long option or debit spread, the sold leg of a credit spread,
  cash-secured put or condor (both sold legs), each leg of a strangle. A strike is used only when its |delta| is
  within ``DELTA_TOLERANCE`` of the target (otherwise that dial setting has no candidate that day).
* **Days to expiry** is a bucket (``DTE_BUCKETS``); inside it the listed expiry nearest the bucket's middle is used.
* **Width** is the strike gap of a spread or a condor's wings as a share of the stock price, placed at the listed
  strike nearest it on the far side.
* **Exit rules** (``EXIT_RULES``) are not chosen at entry: every candidate is labelled with the return each rule would
  have realised on the same path of later closing quotes (``labels``), so the model can learn which way to leave.

Every candidate keeps the exact legs, so it can be valued later from stored quotes. ``content`` is the frozen rule set;
``version_hash`` its fingerprint (a change needs a new ``VERSION`` and a new line in
tests/unit/stocks_spec_fingerprints.json).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

from desk.core.hashing import canonical_json, sha256_hex
from desk.stocks.paper.fills import OPTION_FEE_PER_CONTRACT as FEE

if TYPE_CHECKING:
    from desk.stocks.plays import Table

NAME = "stocks_learner_actions"
VERSION = 1


@dataclass(frozen=True)
class Structure:
    id: str
    title: str
    action: str                     # BUY | SELL (what Plays calls a debit or a credit position)
    deltas: tuple[float, ...]
    widths: tuple[float, ...]       # share of the stock price; (0.0,) when the structure has no width


STRUCTURES: tuple[Structure, ...] = (
    Structure("long_call", "Long call", "BUY", (0.15, 0.30, 0.50, 0.70), (0.0,)),
    Structure("long_put", "Long put", "BUY", (0.15, 0.30, 0.50, 0.70), (0.0,)),
    Structure("bull_call_debit", "Call debit spread", "BUY", (0.50, 0.70), (0.025, 0.05, 0.10)),
    Structure("bear_put_debit", "Put debit spread", "BUY", (0.50, 0.70), (0.025, 0.05, 0.10)),
    Structure("cash_secured_put", "Cash-secured put", "SELL", (0.15, 0.30), (0.0,)),
    Structure("bull_put_credit", "Short put spread", "SELL", (0.15, 0.30), (0.025, 0.05, 0.10)),
    Structure("bear_call_credit", "Short call spread", "SELL", (0.15, 0.30), (0.025, 0.05, 0.10)),
    Structure("iron_condor", "Iron condor", "SELL", (0.15, 0.25), (0.025, 0.05)),
    Structure("long_straddle", "Long straddle", "BUY", (0.50,), (0.0,)),
    Structure("long_strangle", "Long strangle", "BUY", (0.15, 0.30), (0.0,)),
)
BY_STRUCTURE = {s.id: s for s in STRUCTURES}
STRUCTURE_CODE = {s.id: i for i, s in enumerate(STRUCTURES)}
# (id, label, first day, last day): an expiry inside the window, the one nearest its middle
DTE_BUCKETS: tuple[tuple[str, str, int, int], ...] = (
    ("1w", "About a week", 5, 10), ("2w", "Two to three weeks", 11, 21), ("1m", "About a month", 22, 45),
    ("2m", "Two to three months", 46, 90), ("4m", "Four to six months", 91, 180), ("9m", "Six months or more", 181, 400))
BUCKET_CODE = {b[0]: i for i, b in enumerate(DTE_BUCKETS)}
DELTA_TOLERANCE = 0.10              # a strike counts for a target |delta| only this close to it
MAX_LEG_SPREAD = 0.35               # (ask − bid) / mid of every leg of a candidate


@dataclass(frozen=True)
class ExitRule:
    id: str
    title: str
    take_profit: float | None = None    # leave at the first checked close at or above this return on money at risk
    stop: float | None = None           # leave at the first checked close at or below this return
    sessions: int | None = None         # leave at the close this many sessions after the decision


EXIT_RULES: tuple[ExitRule, ...] = (
    ExitRule("hold", "Hold to expiry"),
    ExitRule("tp25", "Take +25%, else hold", take_profit=0.25),
    ExitRule("tp50", "Take +50%, else hold", take_profit=0.50),
    ExitRule("tp100", "Take +100%, else hold", take_profit=1.00),
    ExitRule("sl50", "Stop at −50%, else hold", stop=-0.50),
    ExitRule("tp50_sl50", "Take +50% or stop at −50%", take_profit=0.50, stop=-0.50),
    ExitRule("t5", "Leave after 5 sessions", sessions=5),
    ExitRule("t10", "Leave after 10 sessions", sessions=10),
    ExitRule("t21", "Leave after 21 sessions", sessions=21),
    ExitRule("tp50_t21", "Take +50%, else leave after 21 sessions", take_profit=0.50, sessions=21),
)
EXIT_IDS = tuple(r.id for r in EXIT_RULES)
BY_EXIT = {r.id: r for r in EXIT_RULES}


@dataclass(frozen=True)
class Action:
    structure: str
    delta: float
    width: float
    bucket: str

    @property
    def key(self) -> str:
        return f"{self.structure}|d{round(self.delta * 100)}|w{round(self.width * 1000)}|{self.bucket}"

    def describe(self) -> str:
        s = BY_STRUCTURE[self.structure]
        label = next(b[1] for b in DTE_BUCKETS if b[0] == self.bucket).lower()
        width = f", strikes {self.width:.1%} of the price apart" if self.width else ""
        return f"{s.title} near {self.delta:.2f} delta, {label}{width}"


def parse_key(key: str) -> Action:
    structure, d, w, bucket = key.split("|")
    return Action(structure, int(d[1:]) / 100, int(w[1:]) / 1000, bucket)


def grid() -> list[Action]:
    """Every action, in a fixed order (structure, |delta|, width, expiry bucket)."""
    return [Action(s.id, d, w, b[0]) for s in STRUCTURES for d in s.deltas for w in s.widths for b in DTE_BUCKETS]


def content() -> dict[str, Any]:
    """The frozen rule set (fingerprinted)."""
    return {"version": VERSION, "structures": [[s.id, s.action, list(s.deltas), list(s.widths)] for s in STRUCTURES],
            "dte_buckets": [list(b) for b in DTE_BUCKETS], "delta_tolerance": DELTA_TOLERANCE,
            "max_leg_spread": MAX_LEG_SPREAD, "fee": FEE,
            "exit_rules": [[r.id, r.take_profit, r.stop, r.sessions] for r in EXIT_RULES]}


def version_hash() -> str:
    return sha256_hex(canonical_json(content()))[:16]


# ── composing an action on one stock's priced chain ──────────────────────────────────────────────
def expiry_for(tb: Table, bucket: str) -> float | None:
    """The listed days-to-expiry inside the bucket nearest its middle (None when the chain lists none)."""
    _, _, lo, hi = next(b for b in DTE_BUCKETS if b[0] == bucket)
    days = sorted({float(d) for d in tb.dte if lo <= d <= hi}, key=lambda d: (abs(d - (lo + hi) / 2), d))
    return days[0] if days else None


def _nearest(tb: Table, on: Any, call: bool, target: float, *, side: str | None = None) -> int | None:
    """The quoted contract of this kind nearest ``target`` |delta| (out of the money on ``side`` when given)."""
    mask = on & (tb.call == call) & (tb.spread <= MAX_LEG_SPREAD)
    price = tb.u.price
    if side == "above":
        mask = mask & (tb.k > price)
    elif side == "below":
        mask = mask & (tb.k < price)
    rows = np.where(mask)[0]
    if not len(rows):
        return None
    best = int(min(rows, key=lambda i: (abs(abs(float(tb.delta[i])) - target), float(tb.spread[i]), float(tb.k[i]))))
    return best if abs(abs(float(tb.delta[best])) - target) <= DELTA_TOLERANCE else None


def _beyond(tb: Table, on: Any, call: bool, anchor: int, distance: float, up: bool) -> int | None:
    """The quoted contract of this kind on the far side of ``anchor`` nearest ``distance`` away in strike."""
    mask = on & (tb.call == call) & (tb.spread <= MAX_LEG_SPREAD) & ((tb.k > tb.k[anchor]) if up else (tb.k < tb.k[anchor]))
    rows = np.where(mask)[0]
    if not len(rows):
        return None
    target = float(tb.k[anchor]) + (distance if up else -distance)
    return int(min(rows, key=lambda i: (abs(float(tb.k[i]) - target), float(tb.spread[i]))))


def legs_for(tb: Table, action: Action) -> list[tuple[int, str]] | None:
    """The action's legs as (row in ``tb``, "buy" | "sell") in the order ``plays.finish`` reads them (the bought leg
    of a purchase first, the sold leg of a sale first), or None when the chain cannot supply it fairly."""
    days = expiry_for(tb, action.bucket)
    if days is None:
        return None
    on = tb.dte == days
    price, s, d = tb.u.price, action.structure, action.delta
    width = action.width * price
    chosen: list[tuple[int, str]] | None = None
    if s in ("long_call", "long_put"):
        i = _nearest(tb, on, s == "long_call", d)
        chosen = None if i is None else [(i, "buy")]
    elif s in ("bull_call_debit", "bear_put_debit"):
        call = s == "bull_call_debit"
        b = _nearest(tb, on, call, d)
        far = None if b is None else _beyond(tb, on, call, b, width, call)
        chosen = None if b is None or far is None else [(b, "buy"), (far, "sell")]
    elif s in ("bull_put_credit", "bear_call_credit"):
        call = s == "bear_call_credit"
        short = _nearest(tb, on, call, d, side="above" if call else "below")
        long_ = None if short is None else _beyond(tb, on, call, short, width, call)
        chosen = None if short is None or long_ is None else [(short, "sell"), (long_, "buy")]
    elif s == "cash_secured_put":
        i = _nearest(tb, on, False, d, side="below")
        chosen = None if i is None else [(i, "sell")]
    elif s == "iron_condor":
        sp, sc = _nearest(tb, on, False, d, side="below"), _nearest(tb, on, True, d, side="above")
        if sp is not None and sc is not None:
            lp, lc = _beyond(tb, on, False, sp, width, False), _beyond(tb, on, True, sc, width, True)
            if lp is not None and lc is not None:
                chosen = [(sp, "sell"), (lp, "buy"), (sc, "sell"), (lc, "buy")]
    elif s == "long_straddle":
        c = _nearest(tb, on, True, 0.5)
        if c is not None:
            puts = np.where(on & ~tb.call & (tb.k == tb.k[c]) & (tb.spread <= MAX_LEG_SPREAD))[0]
            chosen = [(c, "buy"), (int(puts[0]), "buy")] if len(puts) else None
    elif s == "long_strangle":
        c, p = _nearest(tb, on, True, d, side="above"), _nearest(tb, on, False, d, side="below")
        chosen = None if c is None or p is None else [(c, "buy"), (p, "buy")]
    if chosen is None or not _fair(tb, s, chosen):
        return None
    return chosen


def _fair(tb: Table, structure: str, legs: Sequence[tuple[int, str]]) -> bool:
    """A position Plays could show: a positive debit below a spread's width, a positive credit below what a sold
    structure can lose, both sides of a condor able to lose (credit below the narrower wing)."""
    buy = BY_STRUCTURE[structure].action == "BUY"
    if buy:
        entry = sum(float(tb.ask[i]) if side == "buy" else -float(tb.bid[i]) for i, side in legs)
    else:
        entry = sum(float(tb.bid[i]) if side == "sell" else -float(tb.ask[i]) for i, side in legs)
    if entry <= 0 or entry * 100 <= FEE * len(legs) * (0 if buy else 1):
        return False
    if structure in ("bull_call_debit", "bear_put_debit", "bull_put_credit", "bear_call_credit"):
        return entry < abs(float(tb.k[legs[0][0]] - tb.k[legs[1][0]]))
    if structure == "iron_condor":
        (sp, _), (lp, _), (sc, _), (lc, _) = legs
        narrow = min(float(tb.k[sp] - tb.k[lp]), float(tb.k[lc] - tb.k[sc]))
        return entry < narrow and float(tb.k[sp]) < float(tb.k[sc])
    if structure == "cash_secured_put":
        return entry < float(tb.k[legs[0][0]])
    return True


__all__ = ["BUCKET_CODE", "BY_EXIT", "BY_STRUCTURE", "DTE_BUCKETS", "EXIT_IDS", "EXIT_RULES", "NAME", "STRUCTURES",
           "STRUCTURE_CODE", "VERSION", "Action", "ExitRule", "Structure", "content", "expiry_for", "grid", "legs_for",
           "parse_key", "version_hash"]
