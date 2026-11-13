"""The Learner PAPER account's decision layer: a contextual bandit over the action space (docs/STOCKS_LEARNER.md →
The Learner account).

**When.** Once a closed session's candidates are recorded (``experience.record_forward``), before the next session
opens. A session decided later is not decided at all (its quotes would be stale).

**What it may trade.** That session's recorded grid candidates (every structure × |delta| × expiry × width the chains
supplied fairly, with their point-in-time features), only defined-risk structures, priced at that session's quotes.
A confirmed "don't" finding (``findings``) removes the candidates it describes.

**Only once a model has earned it (the gate).** The newest frozen model of the current code must have beaten random
on held-out data: its top tenth of each held-out day's candidates against that day's average candidate
(``deciles.top_minus_random``) over at least ``GATE_MIN_DAYS`` held-out days, with the 90% bootstrap range's lower end
above zero, and a positive held-out rank correlation. Otherwise the Learner waits and records why; its random twin
waits with it, so both are judged over the same sessions.

**The rule.** For every candidate the model gives an expected return held to expiry ``mu`` and its 10th and 90th
percentiles. Its outcome spread is ``sigma = (q90 − q10) / 2.563`` (at least ``MIN_SIGMA``), and the evidence ``n`` is
the number of labelled rows the model was trained on in the same region of the action space (structure, |delta|,
expiry bucket: ``model.evidence_cells``). The uncertainty of ``mu`` is ``se = sigma / sqrt(n + 1)``.

* **Exploit** (``1 − EXPLORE_SHARE`` = 85% of each session's new-risk budget): candidates whose lower confidence bound
  ``mu − LCB_Z·se`` is above zero, best bound first: confident positive expectancy.
* **Explore** (``EXPLORE_SHARE`` = 15% of the budget, at most ``EXPLORE_TRADE_RISK`` a trade): candidates that are
  uncertain (lower bound at or below zero) but promising (upper bound ``mu + UCB_Z·se`` above zero), ranked by a
  Thompson draw ``mu + se·z`` (``z`` standard normal, seeded by the session, account and candidate) and taken while the
  draw is above zero. Explored trades are chosen first (at most ``MAX_EXPLORE_NEW`` a session), so the exploited ones
  never crowd out the exploration share.
* **Exit**: the exit rule the model predicts best for that candidate (``best_exit``), hold to expiry when it has none.

**Risk limits** (fixed, of the account's equity when decided): at most ``TRADE_RISK`` (2%) at risk in a trade, fees
and the fill limit included; ``SESSION_RISK`` (8%) of new risk a session; ``TOTAL_RISK`` (30%) at risk in all open
and pending positions; ``STOCK_RISK`` (6%) on one stock; ``SECTOR_RISK`` (12%) in one sector; ``MAX_OPEN`` positions;
one new position a stock a session; at most ``MAX_UNITS`` units. Money at risk is the most a position can lose: what a
bought structure costs, or a sold structure's width less its credit. A sold structure is **fully collateralised**:
its maximum loss is held in cash for as long as it is open (a cash-secured put's is its strike, so on a $50,000
account at 2% a trade only puts on stocks under about $10 fit). Nothing uncovered is ever sold.

**The random twin** (``RANDOM``): the same candidates, sessions and limits; each trade a uniformly random candidate
and a uniformly random exit rule (seeded by the session), sized at ``TRADE_RISK``, until the session's budget is used.

**Recorded.** Every chosen trade with the model's hash, its scores and uncertainty, explore or exploit, the exit
rule, the alternatives weighed and why it was chosen (``stk_learner_decisions``), and every session's outcome,
including a wait and why (``stk_learner_days``), in the PAPER ledger in the same transaction as the orders. The orders
fill like every PAPER option order (``paper.engine``): at the next session's quotes, buying at the ask and selling at
the bid, the usual fees, never on a stale, crossed or unfair quote.

``content`` is the frozen policy; ``version_hash`` its fingerprint. Nothing here changes Plays, another PAPER account,
Learning or any calibration.
"""

from __future__ import annotations

import json
import math
import random
import sqlite3
import time as clock
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import numpy as np

from desk.core.clock import to_iso
from desk.core.hashing import canonical_json, sha256_hex
from desk.stocks import calendar as cal
from desk.stocks.learner import actions as act
from desk.stocks.learner import experience as ex
from desk.stocks.learner import model as md
from desk.stocks.learner import regimes as rg
from desk.stocks.paper import fills
from desk.stocks.paper.strategies import Strategy

NAME = "stocks_learner_policy"
VERSION = 1
ACCOUNT = "learner"
RANDOM = "learner_random"
CAPITAL = 50_000.0
TRADE_RISK = 0.02
EXPLORE_TRADE_RISK = 0.01
SESSION_RISK = 0.08
EXPLORE_SHARE = 0.15
TOTAL_RISK = 0.30
STOCK_RISK = 0.06
SECTOR_RISK = 0.12
MAX_OPEN = 20
MAX_NEW = 6                         # new positions a session in all
MAX_EXPLORE_NEW = 2                 # of which explored (chosen first, within its own 15% budget)
MAX_UNITS = 10
LCB_Z = 1.0
UCB_Z = 2.0
MIN_SIGMA = 0.05
SPREAD_TO_SIGMA = 2.5631            # the 10–90% range of a normal is 2.5631 standard deviations
GATE_MIN_DAYS = 20
CREDIT_FLOOR = 0.90                 # a sold structure fills only for at least 90% of the credit decided on
ALTERNATIVES = 5


def content() -> dict[str, Any]:
    return {"version": VERSION, "capital": CAPITAL, "trade_risk": TRADE_RISK, "explore_trade_risk": EXPLORE_TRADE_RISK,
            "session_risk": SESSION_RISK, "explore_share": EXPLORE_SHARE, "total_risk": TOTAL_RISK,
            "stock_risk": STOCK_RISK, "sector_risk": SECTOR_RISK, "max_open": MAX_OPEN, "max_new": MAX_NEW,
            "max_explore_new": MAX_EXPLORE_NEW, "max_units": MAX_UNITS, "lcb_z": LCB_Z, "ucb_z": UCB_Z, "min_sigma": MIN_SIGMA,
            "gate": {"min_days": GATE_MIN_DAYS, "metric": "deciles.top_minus_random low > 0 and rank_correlation > 0"},
            "credit_floor": CREDIT_FLOOR, "fill_limit": [fills.MAX_ASK_RUN, fills.MIN_ASK_RUN],
            "rule": "exploit by lower confidence bound mu - lcb_z*se > 0; explore by a Thompson draw mu + se*z among "
                    "lcb <= 0 < ucb; se = max(min_sigma, (q90 - q10)/2.5631) / sqrt(n + 1)",
            "random": "uniform candidate, uniform exit rule, trade_risk sizing, same sessions and limits",
            "actions": act.version_hash(), "regimes": rg.version_hash()}


def version_hash() -> str:
    return sha256_hex(canonical_json(content()))[:16]


def _P(**kw: Any) -> tuple[tuple[str, Any], ...]:
    return tuple(sorted(kw.items()))


LEARNER = Strategy(ACCOUNT, 1, "Learner", "option", "learner",
                   "A model of every recorded candidate, choosing trades by a lower confidence bound and exploring a "
                   "fixed 15% of new risk by Thompson sampling, beats a random account of the same risk, the "
                   "rule-based option strategies and SPY",
                   _P(rule="learner_policy", policy=version_hash(), capital=CAPITAL, compare=RANDOM))
LEARNER_RANDOM = Strategy(RANDOM, 1, "Learner's random twin", "option", "learner",
                          "Random grid candidates with random exits at the Learner's risk and on its sessions: the bar "
                          "the Learner must clear", _P(rule="learner_random", policy=version_hash(), capital=CAPITAL),
                          baseline=True)
STRATEGIES: tuple[Strategy, ...] = (LEARNER, LEARNER_RANDOM)
ACCOUNTS = (ACCOUNT, RANDOM)


# ── the gate ─────────────────────────────────────────────────────────────────────────────────────
@dataclass
class Gate:
    ok: bool
    why: str
    model_id: str | None = None
    model_code: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    fitted: md.Fitted | None = None
    metrics: dict[str, Any] = field(default_factory=dict)


def gate(row: sqlite3.Row | Mapping[str, Any] | None, home: Path | None = None, *, load: bool = True) -> Gate:
    """Whether the newest model may trade (see the module docstring), and why not."""
    if row is None:
        return Gate(False, "No model of the current code has been trained yet: the Learner waits for one that beat "
                           "random on held-out data.")
    metrics = json.loads(row["metrics_json"])
    vs = (metrics.get("deciles") or {}).get("top_minus_random") or {}
    rho = metrics.get("rank_correlation")
    detail = {"top_minus_random": vs or None, "rank_correlation": rho, "min_days": GATE_MIN_DAYS}
    base = {"model_id": row["id"], "model_code": row["code_version"], "detail": detail, "metrics": metrics}
    if not vs or int(vs.get("days") or 0) < GATE_MIN_DAYS:
        return Gate(False, f"Model {row['id']} has {int(vs.get('days') or 0)} held-out days of its top tenth against "
                           f"random; the Learner trades once a model has at least {GATE_MIN_DAYS}.", **base)
    if not vs["low"] > 0:
        return Gate(False, f"Model {row['id']} did not beat random on held-out data: its top tenth was "
                           f"{vs['mean'] * 100:+.1f} points against the day's average candidate (90% range "
                           f"{vs['low'] * 100:+.1f} to {vs['high'] * 100:+.1f}, {vs['days']} days). It must be above "
                           "zero across the range.", **base)
    if rho is None or not rho > 0:
        return Gate(False, f"Model {row['id']}'s held-out rank correlation is {rho}; it must be positive.", **base)
    fitted = md.load_models(home, row) if (load and home is not None) else None
    if load and fitted is None:
        return Gate(False, f"Model {row['id']}'s file is missing or does not match its recorded hash; not used.", **base)
    if load and fitted is not None and "expected" not in fitted.models:
        return Gate(False, f"Model {row['id']} has no expected-return model.", **base)
    return Gate(True, f"Model {row['id']} beat random on held-out data: top tenth {vs['mean'] * 100:+.1f} points "
                      f"against the day's average candidate (90% range {vs['low'] * 100:+.1f} to "
                      f"{vs['high'] * 100:+.1f}) over {vs['days']} days; rank correlation {rho:+.2f}.",
                fitted=fitted, **base)


# ── the account ──────────────────────────────────────────────────────────────────────────────────
@dataclass
class State:
    equity: float
    cash: float
    reserved: float                    # collateral of open sold positions and the most pending orders can take
    at_risk: float
    by_symbol: dict[str, float]
    by_sector: dict[str, float]
    open: int
    symbols: set[str]


def account_state(book: Any, account: str) -> State:
    """Equity at the last marks (the starting capital before the account exists), and what is already at risk."""
    has_cash = book.conn.execute("SELECT 1 FROM stk_cash WHERE account=? LIMIT 1", (account,)).fetchone()
    cash = book.cash(account) if has_cash else CAPITAL
    positions = book.positions(account)
    value = sum(p.last_value if p.last_value is not None else p.entry_cost for p in positions)
    by_symbol: dict[str, float] = defaultdict(float)
    by_sector: dict[str, float] = defaultdict(float)
    at_risk = reserved = 0.0
    symbols: set[str] = set()
    for p in positions:
        risk = p.max_loss if p.max_loss is not None else max(p.entry_cost + p.entry_fees, 0.0)
        at_risk += risk
        by_symbol[p.symbol] += risk
        by_sector[str(p.plan.get("sector"))] += risk
        symbols.add(p.symbol)
        if p.entry_cost <= 0:                       # a sold structure: its maximum loss stays in cash
            reserved += risk
    pending = [o for o in book.pending(account) if o.order.get("action") == "ENTER"]
    for o in pending:
        risk = float(o.order.get("risk_usd") or o.order.get("max_debit") or 0.0)
        at_risk += risk
        reserved += risk
        by_symbol[o.order["symbol"]] += risk
        by_sector[str((o.order.get("plan") or {}).get("sector"))] += risk
        symbols.add(o.order["symbol"])
    return State(max(cash + value, 0.0), cash, reserved, at_risk, dict(by_symbol), dict(by_sector),
                 len(positions) + len(pending), symbols)


# ── candidates and scores ────────────────────────────────────────────────────────────────────────
@dataclass
class Option:
    symbol: str
    row: dict[str, Any]
    stock: dict[str, Any]
    market: dict[str, Any]
    x: list[float]
    sector: str
    scores: dict[str, Any] = field(default_factory=dict)
    exit_rule: str = "hold"
    dont: str | None = None

    @property
    def key(self) -> str:
        return str(self.row["key"])

    legs: list[dict[str, Any]] | None = None     # one unit at the session's quotes, once priced

    @property
    def risk_unit(self) -> float:
        """The most one unit can lose, fees and the fill limit included (``legs`` must be priced)."""
        assert self.legs is not None
        if self.row["action"] == "BUY":
            return max_debit(self.legs, 1, "BUY")
        return float(self.row["max_loss"]) + (1 - CREDIT_FLOOR) * float(self.row["entry"]) * fills.CONTRACT


class Pricer:
    """Each candidate's legs at its session's stored quotes, one chain read a stock."""

    def __init__(self, market: Any, session: date) -> None:
        self.market = market
        self.session = session
        self.quotes: dict[str, dict[str, Any] | None] = {}

    def __call__(self, o: Option) -> list[dict[str, Any]] | None:
        if o.symbol not in self.quotes:
            day, chains = self.market.option_chain(o.symbol, self.session)
            self.quotes[o.symbol] = fills.index_chain(chains) if day == self.session else None
        quotes = self.quotes[o.symbol]
        return None if quotes is None else order_legs(quotes, o.row["legs"])


def session_candidates(research: sqlite3.Connection, session: date) -> list[Option]:
    """Every recorded forward grid candidate of ``session`` (not the picks or other books' entries)."""
    out = []
    for g in ex.groups_for(research, "forward", since=session.toordinal(), until=session.toordinal()):
        group = ex.unpack(g["rows_zlib"])
        sector = group["stock"].get("sector")
        for r in group["rows"]:
            if not r["key"]:
                continue
            out.append(Option(g["symbol"], r, group["stock"], group["market"],
                              md.row_vector(group["stock"], group["market"], r["x"], False),
                              "unknown" if sector is None else str(int(sector))))
    return out


def _draw(session: date, account: str, o: Option) -> float:
    rng = random.Random(f"{session.isoformat()}:{account}:{o.symbol}:{o.key}:{ex.legs_key(o.row['legs'])}")  # noqa: S311
    return rng.gauss(0.0, 1.0)


def score(options: Sequence[Option], fitted: md.Fitted, metrics: Mapping[str, Any], session: date,
          donts: Sequence[Mapping[str, Any]] = ()) -> None:
    """The model's view of each candidate, its uncertainty and the evidence behind it (in place)."""
    if not options:
        return
    from desk.stocks.learner import findings as fd

    X = np.asarray([o.x for o in options], dtype=np.float32)
    p = md.predict(fitted, X)
    cells = metrics.get("evidence_cells") or {}
    blocked = fd.filter_reason(donts, X)
    for i, o in enumerate(options):
        view = md.describe(p, i)
        mu, q10, q90 = view.get("expected"), view.get("q10"), view.get("q90")
        n = int(cells.get(md.cell(o.key) or "", 0))
        sigma = max(MIN_SIGMA, (q90 - q10) / SPREAD_TO_SIGMA) if q10 is not None and q90 is not None else 1.0
        se = sigma / math.sqrt(n + 1)
        z = _draw(session, ACCOUNT, o)
        o.scores = {"expected": mu, "q10": q10, "q50": view.get("q50"), "q90": q90,
                    "p_profit_expiry": (view.get("p_profit") or {}).get("exp"), "sigma": round(sigma, 4),
                    "evidence": n, "se": round(se, 4),
                    "lcb": None if mu is None else round(mu - LCB_Z * se, 4),
                    "ucb": None if mu is None else round(mu + UCB_Z * se, 4),
                    "draw": None if mu is None else round(mu + se * z, 4), "z": round(z, 4)}
        best = view.get("best_exit")
        o.exit_rule = best["id"] if best else "hold"
        o.scores["exit_p"] = best.get("p") if best else None
        o.dont = blocked[i]


# ── choosing ─────────────────────────────────────────────────────────────────────────────────────
@dataclass
class Choice:
    option: Option
    mode: str
    units: int
    risk: float
    reason: str
    alternatives: list[dict[str, Any]]


class Limits:
    """The account's fixed limits as the session's trades are added (see the module docstring)."""

    def __init__(self, state: State, pricer: Pricer) -> None:
        self.state = state
        self.pricer = pricer
        e = state.equity
        self.budget = {"exploit": SESSION_RISK * (1 - EXPLORE_SHARE) * e, "explore": SESSION_RISK * EXPLORE_SHARE * e,
                       "random": SESSION_RISK * e}
        self.trade = {"exploit": TRADE_RISK * e, "explore": EXPLORE_TRADE_RISK * e, "random": TRADE_RISK * e}
        self.total = TOTAL_RISK * e - state.at_risk
        self.cash = state.cash - state.reserved
        self.by_symbol = dict(state.by_symbol)
        self.by_sector = dict(state.by_sector)
        self.open = state.open
        self.new = 0
        self.explored = 0
        self.symbols = set(state.symbols)
        self.why: dict[str, int] = defaultdict(int)

    def room(self, o: Option, mode: str) -> float:
        e = self.state.equity
        return min(self.trade[mode], self.budget[mode], self.total, self.cash,
                   STOCK_RISK * e - self.by_symbol.get(o.symbol, 0.0), SECTOR_RISK * e - self.by_sector.get(o.sector, 0.0))

    def size(self, o: Option, mode: str) -> tuple[int, float]:
        if self.open >= MAX_OPEN or self.new >= MAX_NEW or (mode == "explore" and self.explored >= MAX_EXPLORE_NEW):
            self.why["position limits"] += 1
            return 0, 0.0
        if o.symbol in self.symbols:
            self.why["already holds or is buying this stock"] += 1
            return 0, 0.0
        if o.legs is None:
            o.legs = self.pricer(o)
        if o.legs is None:
            self.why["no usable quote for a leg"] += 1
            return 0, 0.0
        unit = o.risk_unit
        room = self.room(o, mode)
        units = min(MAX_UNITS, int(room // unit)) if unit > 0 and room > 0 else 0
        if units < 1:
            self.why["one unit is over a risk limit"] += 1
            return 0, 0.0
        return units, units * unit

    def take(self, o: Option, mode: str, risk: float) -> None:
        self.budget[mode] -= risk
        self.total -= risk
        self.cash -= risk
        self.by_symbol[o.symbol] = self.by_symbol.get(o.symbol, 0.0) + risk
        self.by_sector[o.sector] = self.by_sector.get(o.sector, 0.0) + risk
        self.open += 1
        self.new += 1
        self.explored += mode == "explore"
        self.symbols.add(o.symbol)


def _brief(o: Option, why: str) -> dict[str, Any]:
    s = o.scores
    return {"symbol": o.symbol, "key": o.key, "expected": s.get("expected"), "lcb": s.get("lcb"), "ucb": s.get("ucb"),
            "draw": s.get("draw"), "evidence": s.get("evidence"), "exit": o.exit_rule, "why_not": why}


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:+.0f}%"


def choose(options: Sequence[Option], limits: Limits) -> list[Choice]:
    """The Learner's trades of the session: exploit by lower bound, then explore by Thompson draw."""
    usable = [o for o in options if o.scores.get("expected") is not None and o.dont is None]
    exploit = sorted((o for o in usable if o.scores["lcb"] > 0), key=lambda o: (-o.scores["lcb"], o.symbol, o.key))
    explore = sorted((o for o in usable if o.scores["lcb"] <= 0 < o.scores["ucb"] and o.scores["draw"] > 0),
                     key=lambda o: (-o.scores["draw"], o.symbol, o.key))
    out: list[Choice] = []
    for mode, ranked in (("explore", explore), ("exploit", exploit)):
        for i, o in enumerate(ranked):
            units, risk = limits.size(o, mode)
            if units < 1:
                continue
            limits.take(o, mode, risk)
            s = o.scores
            rule = act.BY_EXIT[o.exit_rule]
            exit_words = f"exit: {rule.title.lower()}" + (f" (the model's best-exit choice, {s['exit_p']:.0%})"
                                                         if s.get("exit_p") is not None else "")
            if mode == "exploit":
                reason = (f"Exploit: expected {_pct(s['expected'])} on the money at risk held to expiry, lower bound "
                          f"{_pct(s['lcb'])} ({s['evidence']:,} similar labelled trades); {exit_words}")
            else:
                reason = (f"Explore: uncertain but promising, expected {_pct(s['expected'])}, upper bound "
                          f"{_pct(s['ucb'])}, Thompson draw {_pct(s['draw'])} with only {s['evidence']:,} similar "
                          f"labelled trades; {exit_words}")
            others = [_brief(x, "ranked lower") for x in ranked[i + 1:i + 1 + ALTERNATIVES]]
            out.append(Choice(o, mode, units, risk, reason, others))
    return out


def choose_random(options: Sequence[Option], limits: Limits, session: date) -> list[Choice]:
    rng = random.Random(f"{session.isoformat()}:{RANDOM}")      # noqa: S311 - a reproducible draw
    pool = sorted(options, key=lambda o: (o.symbol, o.key, ex.legs_key(o.row["legs"])))
    rng.shuffle(pool)
    out = []
    for o in pool:
        if limits.budget["random"] < 1 or limits.new >= MAX_NEW:
            break
        units, risk = limits.size(o, "random")
        if units < 1:
            continue
        limits.take(o, "random", risk)
        rule = rng.choice(act.EXIT_RULES)
        twin = Option(o.symbol, o.row, o.stock, o.market, o.x, o.sector, {}, rule.id, None, o.legs)
        out.append(Choice(twin, "random", units, risk,
                          f"Random baseline: drawn uniformly from the session's {len(options):,} candidates; exit "
                          f"drawn at random: {rule.title.lower()}", []))
    return out


# ── orders ───────────────────────────────────────────────────────────────────────────────────────
def order_legs(quotes: Mapping[str, Any], legs: Sequence[Sequence[Any]]) -> list[dict[str, Any]] | None:
    """The engine's legs (one unit) at the decision session's quotes: bought at the ask, sold at the bid."""
    from desk.stocks.play_tracking import parse_occ

    out = []
    for occ, sign in legs:
        q = quotes.get(occ)
        if q is None or q.bid is None or q.ask is None or q.bid <= 0 or q.ask < q.bid:
            return None
        _, kind, strike, expiration = parse_occ(occ)
        out.append({"occ": occ, "kind": kind, "strike": strike, "expiration": expiration.isoformat(),
                    "quantity": int(sign), "decision_price": float(q.ask if sign > 0 else q.bid),
                    "decision_mid": q.mid})
    return out


def _cents_up(x: float) -> float:
    """Rounded up to the cent, so a fill at exactly the limit is not refused by a float's last digit."""
    return math.ceil(float(x) * 100 - 1e-6) / 100 + 1e-6


def max_debit(legs: Sequence[Mapping[str, Any]], units: int, action: str) -> float:
    """The most the order may pay (debit) or the least credit it may take, fees included."""
    fee = fills.OPTION_FEE_PER_CONTRACT * len(legs)
    if action == "BUY":
        per = sum(leg["quantity"] * (fills.limit(leg["decision_price"]) if leg["quantity"] > 0 else leg["decision_price"])
                  for leg in legs)
        return _cents_up(units * (per * fills.CONTRACT + fee))
    credit = -sum(leg["quantity"] * leg["decision_price"] for leg in legs)
    return _cents_up(units * (-CREDIT_FLOOR * credit * fills.CONTRACT + fee))


def _record_day(conn: sqlite3.Connection, session: date, account: str, now: datetime, status: str, why: str,
                g: Gate | None, detail: Mapping[str, Any]) -> None:
    conn.execute("INSERT OR IGNORE INTO stk_learner_days(session, account, decided_at, policy_version, model_id, "
                 "model_code, status, why, detail_json, source) VALUES (?,?,?,?,?,?,?,?,?,'forward')",
                 (session.toordinal(), account, to_iso(now), version_hash(), None if g is None else g.model_id,
                  None if g is None else g.model_code, status, why, json.dumps(detail, allow_nan=False, default=str)))


def decided(conn: sqlite3.Connection, session: date, account: str) -> bool:
    try:
        return conn.execute("SELECT 1 FROM stk_learner_days WHERE session=? AND account=?",
                            (session.toordinal(), account)).fetchone() is not None
    except sqlite3.OperationalError:
        return True


def _place(book: Any, st: Strategy, session: date, now: datetime, c: Choice, g: Gate | None, equity: float,
           regime: str) -> None:
    o = c.option
    legs = o.legs
    assert legs is not None
    rule = act.BY_EXIT[o.exit_rule]
    a = act.parse_key(o.key)
    plan = {"exit_rule": rule.id, "take_profit": rule.take_profit, "stop": rule.stop, "sessions": rule.sessions,
            "decided": session.isoformat(), "sector": o.sector, "mode": c.mode, "action": o.row["action"],
            "regime": regime, "risk_usd": round(c.risk, 2)}
    evidence = {"learner": True, "mode": c.mode, "model_id": None if g is None else g.model_id,
                "policy": version_hash(), "action_key": o.key, "dials": a.describe(), "units": c.units,
                "risk_usd": round(c.risk, 2), "exit_rule": rule.id, "scores": o.scores, "legs": legs,
                "regime": regime}
    did = book.decide(now, session, st.id, st.spec_sha256, o.symbol, "ENTER", [c.reason], evidence)
    cur = book.conn.execute(
        "INSERT INTO stk_learner_decisions(session, account, decided_at, policy_version, model_id, symbol, action_key, "
        "mode, exit_rule, units, risk_usd, equity_usd, regime, scores_json, alternatives_json, reason, "
        "paper_decision_id, source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'forward')",
        (session.toordinal(), st.id, to_iso(now), version_hash(), None if g is None or c.mode == "random" else g.model_id,
         o.symbol, o.key, c.mode, rule.id, c.units, round(c.risk, 2), round(equity, 2), regime,
         json.dumps(o.scores, allow_nan=False), json.dumps(c.alternatives, allow_nan=False), c.reason, did))
    plan["learner_decision"] = int(cur.lastrowid or 0)
    book.place(did, st.id, now, session, {"asset": "option", "action": "ENTER", "symbol": o.symbol,
                                          "structure": a.structure, "legs": legs, "units": c.units,
                                          "max_debit": max_debit(legs, c.units, o.row["action"]),
                                          "risk_usd": round(c.risk, 2), "plan": plan, "strict_quotes": True})


def decide(research: sqlite3.Connection, ledger: sqlite3.Connection, market: Any, home: Path, session: date,
           now: datetime, *, stop: bool = False) -> dict[str, Any]:
    """Decide ``session`` for the Learner and its random twin (once; see the module docstring)."""
    from desk.stocks.learner import findings as fd
    from desk.stocks.paper.book import PaperBook

    started = clock.monotonic()
    book = PaperBook(ledger)
    todo = [st for st in STRATEGIES if not decided(ledger, session, st.id)]
    if not todo:
        return {"session": session.isoformat(), "decided": False, "reason": "already decided"}
    opens = datetime.combine(cal.next_trading_day(session), time(9, 30), cal.NEW_YORK)
    options = session_candidates(research, session)
    regime = rg.label(rg.codes(options[0].market)) if options else "unknown"
    g = gate(md.latest(research), home)
    why = None
    if now >= opens:
        why = f"Decided too late: the next session opened at {opens.isoformat()}; a stale session is not traded."
    elif stop:
        why = "The STOP file is present: no new entries."
    elif not options:
        why = "No candidates were recorded for this session."
    elif not g.ok:
        why = g.why
    out: dict[str, Any] = {"session": session.isoformat(), "decided": True, "candidates": len(options),
                           "gate": g.why, "regime": regime}
    if why is not None:
        with book.atomic():
            for st in todo:
                note = why if st.id == ACCOUNT else f"Waits with the Learner (same sessions): {why}"
                _record_day(ledger, session, st.id, now, "waiting", note, g, {"candidates": len(options),
                                                                           "gate": g.detail, "regime": regime})
        return {**out, "status": "waiting", "why": why}
    assert g.fitted is not None
    donts = fd.confirmed_donts(research)
    score(options, g.fitted, g.metrics, session, donts)
    scored = clock.monotonic() - started
    results: dict[str, Any] = {}
    pricer = Pricer(market, session)
    with book.atomic():
        for st in todo:
            state = account_state(book, st.id)
            limits = Limits(state, pricer)
            if st.id == ACCOUNT:
                chosen = choose(options, limits)
            else:
                chosen = choose_random([o for o in options if o.dont is None], limits, session)
            for c in chosen:
                _place(book, st, session, now, c, g, state.equity, regime)
            placed = chosen
            usable = [o for o in options if o.scores.get("expected") is not None]
            detail = {"candidates": len(options), "scored": len(usable),
                      "filtered_by_dont": sum(o.dont is not None for o in options),
                      "exploit_eligible": sum(1 for o in usable if o.dont is None and o.scores["lcb"] > 0),
                      "explore_eligible": sum(1 for o in usable if o.dont is None and o.scores["lcb"] <= 0
                                              < o.scores["ucb"] and o.scores["draw"] > 0),
                      "equity": round(state.equity, 2), "at_risk_before": round(state.at_risk, 2),
                      "new_risk": round(sum(c.risk for c in placed), 2),
                      "explore_risk": round(sum(c.risk for c in placed if c.mode == "explore"), 2),
                      "budgets": {k: round(v, 2) for k, v in limits.budget.items()}, "skipped": dict(limits.why),
                      "regime": regime, "seconds_scoring": round(scored, 2),
                      "seconds": round(clock.monotonic() - started, 2), "gate": g.detail}
            status = "traded" if placed else "nothing"
            why_day = (f"{len(placed)} trade{'s' if len(placed) != 1 else ''} with model {g.model_id}" if placed else
                       "No candidate cleared the rule within the limits" if st.id == ACCOUNT else
                       "No random candidate fitted within the limits")
            _record_day(ledger, session, st.id, now, status, why_day, g, detail)
            results[st.id] = {"status": status, "trades": len(placed),
                              "explore": sum(c.mode == "explore" for c in placed), **detail}
    return {**out, "status": "decided", "accounts": results}


__all__ = ["ACCOUNT", "ACCOUNTS", "CAPITAL", "EXPLORE_SHARE", "GATE_MIN_DAYS", "LEARNER", "LEARNER_RANDOM", "NAME",
           "RANDOM", "STRATEGIES", "VERSION", "Choice", "Gate", "Limits", "Option", "Pricer", "State", "account_state", "choose",
           "choose_random", "content", "decide", "decided", "gate", "max_debit", "order_legs", "score",
           "session_candidates", "version_hash"]
