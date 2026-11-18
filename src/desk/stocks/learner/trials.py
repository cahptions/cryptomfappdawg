"""What the Learner tries next: every confirmed "do" finding becomes a **trial** (docs/STOCKS_LEARNER.md → From
findings to trials). This closes the loop from the Learning report back to trading: a finding is not only written down,
it is tested forward on new trades.

**Derived, never edited.** A trial is the finding's own rule, frozen: ``derive`` turns a confirmed finding into a spec
(the rule, its plain words, the finding it came from, this module's version) whose SHA-256 is its fingerprint. The same
finding always gives the same trial; nothing in it is chosen by hand.

**Two forward tests, both graded like everything else.**

* **A Plays section** ``learned_<finding>`` titled "Learned: <the rule in words>", clearly labelled a trial with the
  finding's evidence. Its plays are today's candidates matching the rule: the stocks in a fixed seeded order
  (session and trial), one matching trade a stock (its matching dial settings in the same seeded order), at most
  ``plays.PER_CATEGORY``. A fair sample, not a ranking: the finding is about the average matching trade. Recorded with
  the session, twinned and graded at expiry like every section.
* **A PAPER account** with the same id, trading in the PAPER ledger beside the Learner: each closing session's recorded
  grid candidates matching the rule (not matching any confirmed "don't"), in a seeded order, sized and limited like the
  Learner's random twin (2% a trade, 8% a session, the Learner's limits), held to expiry, graded against the random
  twin. Its entries use the fill model's limit like the Learner's (``policy``).

**Bounded.** At most ``MAX_LIVE`` trials are live; a confirmed finding waits (oldest confirmation first) until a slot
frees. Each trial created is added to the persistent trial counter (``overfit``, kind "finding"), so the bar for every
later finding rises with it.

**Retired automatically** when its finding is retired, or when either forward record fails: over at least
``FAIL_DAYS`` graded sessions (Plays: plays resolved at expiry, by pick session; PAPER: decision days whose trades
have all closed at real fills) the mean of the daily means is at or below zero, the way a confirmed finding is
retired. A retired trial stops showing and stops opening positions (its open ones run to expiry); every stage is a
new row.

**Don't findings** do not become trials: a play on the board matching a confirmed "don't" carries a visible warning
(``warnings``), and the Learner's accounts already skip such trades.
"""

from __future__ import annotations

import json
import math
import random
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from statistics import fmean
from typing import TYPE_CHECKING, Any

import numpy as np

from desk.core.clock import to_iso
from desk.core.hashing import canonical_json, sha256_hex
from desk.stocks.learner import actions as act
from desk.stocks.learner import features as ft
from desk.stocks.learner import findings as fd
from desk.stocks.learner import model as md
from desk.stocks.paper.strategies import Strategy

if TYPE_CHECKING:
    from desk.stocks.plays import Candidate, Table

NAME = "stocks_learner_trials"
VERSION = 1
MAX_LIVE = 5
FAIL_DAYS = 20
PREFIX = "learned_"
LABEL = ("Trial: a rule the Learner found on held-out data and confirmed on later, untouched data. It is being "
         "tested forward on today's matching trades. Not proven until its own forward record says so.")


def content() -> dict[str, Any]:
    return {"version": VERSION, "max_live": MAX_LIVE, "fail_days": FAIL_DAYS, "prefix": PREFIX,
            "from": "each confirmed 'do' finding (findings v2: Benjamini-Hochberg, confirmed on later untouched days, "
                    "deflated Sharpe ratio >= 0.95), oldest confirmation first while fewer than max_live are live",
            "spec": "finding id, its rule, its words, the findings version, this version; fingerprint = sha256",
            "plays": "today's candidates matching the rule: stocks in a seeded order (session, trial), one matching "
                     "trade a stock (its dial settings in the same seeded order), at most plays.PER_CATEGORY",
            "paper": "the session's recorded grid candidates matching the rule and no confirmed don't, seeded order, "
                     "the random twin's sizing and the Learner's limits, held to expiry, compared with learner_random",
            "retire": "the finding retired, or over >= fail_days graded days (Plays at expiry by pick session; PAPER "
                      "by decision day, all closed, real fills) the mean of daily means <= 0",
            "counter": "each trial created is one more 'finding' trial in the persistent counter",
            "dont": "plays matching a confirmed don't carry a warning; the Learner's accounts skip them"}


def version_hash() -> str:
    return sha256_hex(canonical_json(content()))[:16]


# ── deriving a trial from a finding ──────────────────────────────────────────────────────────────
def derive(finding: Mapping[str, Any]) -> dict[str, Any]:
    """The trial's frozen spec, a pure function of the finding (its id, rule and rules version)."""
    rule = json.loads(canonical_json(finding["rule"]))
    spec = {"version": VERSION, "id": PREFIX + str(finding["id"])[:8], "finding": str(finding["id"]),
            "kind": "do", "rule": rule, "words": fd.describe(rule), "rules_version": finding.get("rules_version"),
            "trials": version_hash()}
    return {**spec, "fingerprint": sha256_hex(canonical_json(spec))[:16]}


def strategy(spec: Mapping[str, Any]) -> Strategy:
    """The trial's PAPER account (style "learner": its entries come from ``decide``, its exits from the engine)."""
    from desk.stocks.learner import policy

    params = (("capital", policy.CAPITAL), ("compare", policy.RANDOM), ("exit_rule", "hold"),
              ("finding", spec["finding"]), ("fingerprint", spec["fingerprint"]), ("rule", "learned_trial"))
    return Strategy(spec["id"], 1, f"Learned: {spec['words']}", "option", "learner",
                    f"The Learner's confirmed finding holds forward: {spec['words']} make money on average, held to "
                    "expiry, beating random candidates of the same risk", params, learned=True)


# ── the store ────────────────────────────────────────────────────────────────────────────────────
def specs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every trial ever created, oldest first, with its latest stage ("live" or "retired") and its history."""
    try:
        rows = conn.execute("SELECT * FROM stk_learn_trial_specs ORDER BY created_at, id").fetchall()
        stages = conn.execute("SELECT * FROM stk_learn_trial_status ORDER BY at").fetchall()
    except sqlite3.OperationalError:
        return []
    by: dict[str, list[dict[str, Any]]] = {}
    for s in stages:
        by.setdefault(s["trial_id"], []).append({"status": s["status"], "at": s["at"], "reason": s["reason"],
                                                 "evidence": json.loads(s["evidence_json"])})
    out = []
    for r in rows:
        history = by.get(r["id"], [])
        retired = next((h for h in history if h["status"] == "retired"), None)
        out.append({**json.loads(r["spec_json"]), "created_at": r["created_at"],
                    "status": "retired" if retired else "live", "retired": retired, "stages": history,
                    "evidence": json.loads(r["evidence_json"])})
    return out


def live(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [s for s in specs(conn) if s["status"] == "live"]


def strategies(conn: sqlite3.Connection) -> tuple[list[Strategy], set[str]]:
    """Every trial's PAPER account (retired ones keep exiting) and the retired ids."""
    out, retired = [], set()
    for s in specs(conn):
        out.append(strategy(s))
        if s["status"] == "retired":
            retired.add(s["id"])
    return out, retired


def _stage(conn: sqlite3.Connection, trial: str, status: str, reason: str, evidence: Mapping[str, Any],
           now: datetime) -> bool:
    cur = conn.execute("INSERT OR IGNORE INTO stk_learn_trial_status(trial_id, status, at, reason, evidence_json) "
                       "VALUES (?,?,?,?,?)", (trial, status, to_iso(now), reason,
                                              json.dumps(evidence, allow_nan=False, default=str)))
    return bool(cur.rowcount)


def _evidence(f: Mapping[str, Any]) -> dict[str, Any]:
    """The finding's evidence as the trial shows it: discovery and confirmation, plain numbers."""
    def brief(ev: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if not ev:
            return None
        d = ev.get("deflated") or {}
        return {k: ev.get(k) for k in ("mean", "low", "high", "rows", "days", "first", "last", "p")} | {
            "dsr": d.get("dsr"), "trials": d.get("trials")}

    confirmed = next((s for s in f.get("stages") or [] if s["status"] == "confirmed"), None)
    return {"sentence": f.get("words"), "discovery": brief(f.get("evidence")),
            "confirmation": brief(confirmed["evidence"] if confirmed else None),
            "confirmed_at": confirmed["at"] if confirmed else None}


def forward(research: sqlite3.Connection, ledger: sqlite3.Connection | None, trial_id: str) -> dict[str, Any]:
    """The trial's two forward records: its Plays section (graded at expiry, by pick session) and its PAPER account
    (realised by decision day, every trade closed, real fills only)."""
    plays: dict[int, list[float]] = {}
    try:
        for s, r in research.execute("SELECT session, net_return FROM stk_play_outcomes WHERE category=?", (trial_id,)):
            plays.setdefault(int(s), []).append(float(r))
    except sqlite3.OperationalError:
        pass
    out: dict[str, Any] = {"plays": _record({s: fmean(v) for s, v in plays.items()},
                                            sum(len(v) for v in plays.values())), "paper": None}
    if ledger is not None:
        from desk.stocks.learner.promotion import daily
        from desk.stocks.paper.book import PaperBook

        try:
            got, counted, still_open = daily(PaperBook(ledger), trial_id)
        except sqlite3.OperationalError:
            got, counted, still_open = {}, 0, 0
        out["paper"] = {**_record(got, counted), "open": still_open}
    return out


def _record(by_day: Mapping[int, float], trades: int) -> dict[str, Any]:
    days = len(by_day)
    mean = fmean(by_day.values()) if by_day else None
    return {"days": days, "trades": trades, "mean": None if mean is None else round(mean, 4), "need": FAIL_DAYS,
            "fails": bool(days >= FAIL_DAYS and mean is not None and mean <= 0)}


def words_forward(f: Mapping[str, Any]) -> str:
    """The forward record in one plain sentence."""
    parts = []
    for name, rec in (("Plays", f.get("plays")), ("PAPER", f.get("paper"))):
        if not rec:
            continue
        if not rec["days"]:
            parts.append(f"{name}: nothing graded yet")
        else:
            parts.append(f"{name}: {rec['mean'] * 100:+.0f}% average over {rec['days']} graded "
                         f"day{'s' if rec['days'] != 1 else ''} ({rec['trades']} trade{'s' if rec['trades'] != 1 else ''})"
                         + ("" if rec["days"] >= FAIL_DAYS else f"; judged from {FAIL_DAYS} days"))
    return "; ".join(parts) + "." if parts else "No forward record yet."


def sync(research: sqlite3.Connection, ledger: sqlite3.Connection | None, now: datetime,
         market: Any = None) -> dict[str, Any]:
    """Retire trials whose finding retired or whose forward record failed; then create trials for confirmed "do"
    findings (oldest confirmation first) while fewer than ``MAX_LIVE`` are live. Idempotent."""
    from desk.stocks.learner import overfit as of

    findings = {f["id"]: f for f in fd.current(research)}
    out: dict[str, Any] = {"retired": [], "created": [], "waiting": 0}
    for t in live(research):
        f = findings.get(t["finding"])
        if f is None or f["status"] != "confirmed":
            why = f"its finding is {f['status'] if f else 'gone'}"
            if _stage(research, t["id"], "retired", f"Retired: {why}.", {"finding": f["status"] if f else None}, now):
                out["retired"].append(t["id"])
            continue
        rec = forward(research, ledger, t["id"])
        failed = [k for k in ("plays", "paper") if (rec.get(k) or {}).get("fails")]
        if failed:
            names = " and ".join("its Plays section" if k == "plays" else "its PAPER account" for k in failed)
            if _stage(research, t["id"], "retired", f"Retired: {names} averaged at or below zero over at least "
                                                    f"{FAIL_DAYS} graded days.", rec, now):
                out["retired"].append(t["id"])
    have = {t["finding"] for t in specs(research)}
    waiting = sorted((f for f in findings.values() if f["kind"] == "do" and f["status"] == "confirmed"
                      and f["id"] not in have), key=lambda f: (_evidence(f)["confirmed_at"] or "", f["id"]))
    room = MAX_LIVE - len(live(research))
    for f in waiting[:max(room, 0)]:
        spec = derive(f)
        research.execute("INSERT OR IGNORE INTO stk_learn_trial_specs(id, finding_id, version, fingerprint, spec_json, "
                         "evidence_json, created_at) VALUES (?,?,?,?,?,?,?)",
                         (spec["id"], spec["finding"], VERSION, spec["fingerprint"], canonical_json(spec),
                          json.dumps(_evidence(f), allow_nan=False, default=str), to_iso(now)))
        _stage(research, spec["id"], "live", "Created from a confirmed finding.", {"finding": f["id"]}, now)
        of.record(research, "finding", [{"name": f"trial:{spec['id']}", "sharpe": None, "observations": 0,
                                         "trial": spec["fingerprint"]}], f.get("model_id"), now)
        if market is not None:
            market.register_spec(f"stocks_trial_{spec['id']}", VERSION, spec, now)
        out["created"].append(spec["id"])
    out["waiting"] = max(0, len(waiting) - max(room, 0))
    return out


# ── the PAPER accounts ───────────────────────────────────────────────────────────────────────────
def decide(research: sqlite3.Connection, ledger: sqlite3.Connection, market: Any, session: date, now: datetime, *,
           stop: bool = False) -> dict[str, Any]:
    """Decide ``session`` for every live trial's PAPER account (once each; see the module docstring)."""
    from datetime import time

    from desk.stocks import calendar as cal
    from desk.stocks.learner import policy
    from desk.stocks.learner import regimes as rg
    from desk.stocks.paper.book import PaperBook

    todo = [t for t in live(research) if not policy.decided(ledger, session, t["id"])]
    if not todo:
        return {"decided": False}
    book = PaperBook(ledger)
    options = policy.session_candidates(research, session)
    regime = rg.label(rg.codes(options[0].market)) if options else "unknown"
    opens = datetime.combine(cal.next_trading_day(session), time(9, 30), cal.NEW_YORK)
    why = ("Decided too late: the next session had opened." if now >= opens else
           "The STOP file is present: no new entries." if stop else
           "No candidates were recorded for this session." if not options else None)
    X = np.asarray([o.x for o in options], dtype=np.float32) if options else np.zeros((0, len(md.FEATURES)), np.float32)
    blocked = fd.filter_reason(fd.confirmed_donts(research), X) if options else []
    pricer = policy.Pricer(market, session)
    table = policy.fill_table(research, session)
    out: dict[str, Any] = {}
    with book.atomic():
        for t in todo:
            st = strategy(t)
            if why is not None:
                policy._record_day(ledger, session, st.id, now, "waiting", why, None, {"candidates": len(options)})
                out[st.id] = {"status": "waiting", "why": why}
                continue
            hit = fd.matches(t["rule"], X)
            pool = sorted((o for i, o in enumerate(options) if hit[i] and blocked[i] is None),
                          key=lambda o: (o.symbol, o.key))
            random.Random(f"{session.isoformat()}:{st.id}").shuffle(pool)      # noqa: S311 - a reproducible order
            state = policy.account_state(book, st.id)
            limits = policy.Limits(state, pricer)
            placed = []
            for o in pool:
                if limits.budget["random"] < 1 or limits.new >= policy.MAX_NEW:
                    break
                units, risk = limits.size(o, "random")
                if units < 1:
                    continue
                limits.take(o, "random", risk)
                trade = policy.Option(o.symbol, o.row, o.stock, o.market, o.x, o.sector, {}, "hold", None, o.legs,
                                      o.index, o.price)
                c = policy.Choice(trade, "random", units, risk,
                                  f"Trial of a learned rule: {t['words']}; drawn in a fixed random order from the "
                                  f"session's {len(pool):,} matching candidates; held to expiry", [])
                policy._place(book, st, session, now, c, None, state.equity, regime, table, pricer)
                placed.append(c)
            status = "traded" if placed else "nothing"
            detail = {"candidates": len(options), "matching": len(pool), "new_risk": round(sum(c.risk for c in placed), 2),
                      "skipped": dict(limits.why), "regime": regime, "fingerprint": t["fingerprint"]}
            policy._record_day(ledger, session, st.id, now, status,
                               f"{len(placed)} trade{'s' if len(placed) != 1 else ''} matching the learned rule"
                               if placed else "No matching candidate fitted within the limits", None, detail)
            out[st.id] = {"status": status, "trades": len(placed), "matching": len(pool)}
    return out


# ── the Plays section ────────────────────────────────────────────────────────────────────────────
class Featurer:
    """The Learner's point-in-time features for plays on a board's tables, as the experience log computes them (the
    stock and market groups as of the previous close; each contract priced by ``plays.finish`` with no calibration)."""

    def __init__(self, market: Any, research: Any, tables: Sequence[Table], session: date, now: datetime,
                 rate: float, outlook: Any = None) -> None:
        from desk.stocks import plays as pl

        self.ctx = pl.Context(session, now, rate, None, 0.0, None, {}, [])
        self.ctx.outlook = outlook
        self.reader = ft.Reader(market.conn, research.conn, session)
        spy = next((tb.u for tb in tables if tb.u.symbol == "SPY"), None)
        self.market_x = ft.market_features(self.reader, spy, rate)
        self.stocks: dict[str, dict[str, float | None]] = {}

    def stock(self, tb: Table) -> dict[str, float | None]:
        if tb.u.symbol not in self.stocks:
            self.stocks[tb.u.symbol] = ft.stock_features(self.reader, tb.u, self.ctx.rate)
        return self.stocks[tb.u.symbol]

    def vector(self, tb: Table, row: Any) -> np.ndarray:
        return np.asarray([md.row_vector(self.stock(tb), self.market_x, row.x, False)], dtype=np.float32)


def _prefilter(rule: Mapping[str, Any], known: Mapping[str, float | None]) -> bool:
    """Whether the rule could still match given the inputs known before pricing (unknown ones pass)."""
    for c in rule["conditions"]:
        if c["f"] not in known:
            continue
        v = known[c["f"]]
        if v is None or not math.isfinite(float(v)):
            return False
        if "in" in c and float(v) not in {float(x) for x in c["in"]}:
            return False
        if c.get("gt") is not None and not float(v) > c["gt"]:
            return False
        if c.get("le") is not None and not float(v) <= c["le"]:
            return False
    return True


def candidates(spec: Mapping[str, Any], tables: Sequence[Table], featurer: Featurer, session: date,
               limit: int) -> list[Candidate]:
    """Today's trades matching the trial's rule: one a stock, stocks and dial settings in a seeded order."""
    from desk.stocks import plays as pl
    from desk.stocks.learner import experience as ex
    from desk.stocks.learner import regimes as rg

    rule = spec["rule"]
    structure = act.STRUCTURES[int(rule["structure"])].id
    actions = [a for a in act.grid() if a.structure == structure]
    rng = random.Random(f"{session.isoformat()}:{spec['id']}")       # noqa: S311 - a reproducible sample
    order = sorted(tables, key=lambda tb: tb.u.symbol)
    rng.shuffle(order)
    regime = rg.codes(featurer.market_x)
    out: list[Candidate] = []
    for tb in order:
        if len(out) >= limit:
            break
        stock = featurer.stock(tb)
        known = {**stock, **featurer.market_x, **regime}
        if not _prefilter(rule, known):
            continue
        mine = [a for a in actions if _prefilter(rule, {"bucket": act.BUCKET_CODE[a.bucket],
                                                         "target_delta": a.delta})]
        random.Random(f"{session.isoformat()}:{spec['id']}:{tb.u.symbol}").shuffle(mine)  # noqa: S311
        for a in mine:
            legs = act.legs_for(tb, a)
            if legs is None:
                continue
            row = ex.priced(tb, a.structure, legs, featurer.ctx, a.key, f"trial:{spec['id']}", a)
            if row is None or not fd.matches(rule, featurer.vector(tb, row))[0]:
                continue
            out.append(pl.Candidate(spec["id"], tb, a.structure, legs, float(limit - len(out)),
                                    [f"Matches the learned rule: {spec['words']}", f"Dials: {a.describe()}"]))
            break
    return out


def card(spec: Mapping[str, Any], research: sqlite3.Connection, ledger: sqlite3.Connection | None) -> dict[str, Any]:
    """What the Plays section and the Learning report show about a trial."""
    rec = forward(research, ledger, spec["id"])
    return {"id": spec["id"], "finding": spec["finding"], "words": spec["words"], "fingerprint": spec["fingerprint"],
            "version": spec["version"], "created_at": spec.get("created_at"), "status": spec.get("status", "live"),
            "label": LABEL, "evidence": spec.get("evidence") or {}, "forward": rec, "forward_words": words_forward(rec),
            "retired": spec.get("retired")}


def section(spec: Mapping[str, Any], trial: Mapping[str, Any]) -> dict[str, Any]:
    """The Plays category entry of a trial (its plays are added by ``plays.build``)."""
    ev = trial["evidence"]
    conf = ev.get("confirmation") or {}
    disc = ev.get("discovery") or {}
    proof = (f"Found on held-out data: {disc['mean'] * 100:+.0f}% average held to expiry over {disc['days']} days"
             if disc.get("mean") is not None else "Found on held-out data")
    if conf.get("mean") is not None:
        proof += f"; confirmed on {conf['days']} later untouched days at {conf['mean'] * 100:+.0f}%"
    if disc.get("dsr") is not None:
        proof += f"; deflated Sharpe ratio {disc['dsr']:.2f} after {disc.get('trials') or 0:,} rules tried"
    return {"id": spec["id"], "title": f"Learned: {spec['words']}", "emoji": "🧪",
            "blurb": "A trial of something the Learner learned. " + proof + ".",
            "basis": (f"Rule (frozen, fingerprint {spec['fingerprint']}): {spec['words']}. Today's matching trades, "
                      "one a stock, in a fixed random order: the finding is about the average matching trade, so "
                      "this is a fair sample, not a ranking. Held to expiry, as the finding was measured."),
            "empty": "No trade on today's chains matches the learned rule.", "trial": trial}


# ── don't warnings ───────────────────────────────────────────────────────────────────────────────
def warnings(donts: Sequence[Mapping[str, Any]], plays: Sequence[dict[str, Any]], tables: Sequence[Table],
             featurer: Featurer) -> int:
    """Mark every play matching a confirmed "don't" finding with ``dont_warning`` (in place); returns how many."""
    from desk.stocks.learner import experience as ex

    if not donts:
        return 0
    by = {tb.u.symbol: tb for tb in tables}
    n = 0
    for p in plays:
        tb = by.get(p["symbol"])
        if tb is None:
            continue
        row = ex.pick_row(tb, featurer.ctx, p["legs"], "board")
        if row is None:
            continue
        X = featurer.vector(tb, row)
        for f in donts:
            if fd.matches(f["rule"], X)[0]:
                ev = f.get("evidence") or {}
                p["dont_warning"] = {
                    "finding": f["id"], "words": fd.describe(f["rule"]),
                    "text": (f"The Learner found that {fd.describe(f['rule']).lower()} did worse than a random "
                             "candidate"
                             + (f" ({ev['mean'] * 100:+.0f}% average held to expiry over {ev['days']} days)"
                                if ev.get("mean") is not None else "")
                             + ", and confirmed it on later data. Its own accounts skip trades like this one.")}
                n += 1
                break
    return n


__all__ = ["FAIL_DAYS", "LABEL", "MAX_LIVE", "NAME", "PREFIX", "VERSION", "Featurer", "candidates", "card", "content",
           "derive", "forward", "live", "section", "specs", "strategies", "strategy", "sync", "version_hash",
           "warnings", "words_forward"]
