"""The Learner's graduation to Plays (docs/STOCKS_LEARNER.md → Learner picks on Plays).

**Graduated** when the current Learner version (model code + policy) is confirmed by its preregistered forward test
(``promotion`` v2: its first 20 entry days beat its random twin and the rule-based option strategies, both 90% ranges
above zero, deflated Sharpe ratio at least 0.95). From then its daily picks are a real Plays section, "Learner picks",
recorded and graded at expiry like every section. Until then they are a shadow preview on the Learner page only.

**Demoted** automatically when its record falls below that same bar again: at each PAPER step the latest ``WINDOW``
entry days whose positions have all closed are judged by the same test (``promotion.judge`` on those days); a failure
demotes it (the section goes away), and a later window that passes graduates it again. A new version (a new model code
or policy) starts from nothing: it must be confirmed itself. Every change of stage is a new row
(``stk_learn_graduation``). ``content`` is the frozen rule.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from typing import Any

from desk.core.clock import to_iso
from desk.core.hashing import canonical_json, sha256_hex
from desk.stocks.learner import actions as act
from desk.stocks.learner import model as md
from desk.stocks.learner import overfit as of
from desk.stocks.learner import policy
from desk.stocks.learner import promotion as pr

NAME = "stocks_learner_graduation"
VERSION = 1
WINDOW = pr.FORWARD_DAYS
SECTION = "learner_picks"
PICK_AGE = 5                         # sessions: older decisions are not shown as today's picks


def content() -> dict[str, Any]:
    return {"version": VERSION, "window": WINDOW, "section": SECTION, "pick_age": PICK_AGE,
            "graduate": "the current version (model code + policy) confirmed by promotion v2",
            "demote": "the latest `window` entry days whose positions have all closed fail promotion v2's bar "
                      "(both 90% ranges above zero and the deflated Sharpe ratio >= 0.95)",
            "again": "a demoted version graduates again when a later window passes the same bar",
            "promotion": pr.version_hash()}


def version_hash() -> str:
    return sha256_hex(canonical_json(content()))[:16]


def current() -> tuple[str, str]:
    return md.version_hash(), policy.version_hash()


def stages(research: sqlite3.Connection, key: tuple[str, str] | None = None) -> list[dict[str, Any]]:
    code, pv = key or current()
    try:
        rows = research.execute("SELECT * FROM stk_learn_graduation WHERE model_code=? AND policy_version=? ORDER BY at",
                                (code, pv)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [{"stage": r["stage"], "at": r["at"], **json.loads(r["detail_json"])} for r in rows]


def status(research: sqlite3.Connection) -> dict[str, Any]:
    """Whether the current version's picks are on Plays, since when and why (read-only)."""
    history = stages(research)
    confirmed = pr.stages(research).get(current(), {}).get("confirmed")
    last = history[-1] if history else None
    graduated = bool(last and last["stage"] == "graduated")
    if last is not None and graduated:
        why = (f"Graduated {last['at'][:10]}: this version beat its random twin and the rule-based strategies on "
               f"{WINDOW} untouched forward entry days (deflated Sharpe ratio at least {of.REQUIRED:.2f}). Its picks are "
               f"a Plays section, graded like the others; it is demoted if its latest {WINDOW} entry days fall below "
               "that bar.")
    elif last:
        why = (f"Demoted {last['at'][:10]}: its latest {WINDOW} entry days fell below the bar it graduated on "
               f"({last.get('why', 'see the forward test')}). Its picks are a preview here only until a later window "
               "passes again.")
    elif confirmed:
        why = "Confirmed; it graduates at the next PAPER step."
    else:
        why = (f"Not graduated: this version must first beat its random twin and the rule-based strategies on its "
               f"first {WINDOW} forward entry days (deflated Sharpe ratio at least {of.REQUIRED:.2f}). Until then its "
               "picks are a shadow preview on this page only.")
    return {"graduated": graduated, "stage": last["stage"] if last else ("confirmed" if confirmed else "not yet"),
            "since": last["at"] if last else None, "why": why, "version": "/".join(current()),
            "stages": history, "rule": {"window": WINDOW, "required_dsr": of.REQUIRED}}


def latest_window(book: Any, decisions: dict[int, int]) -> dict[int, int]:
    """The decisions of the latest ``WINDOW`` entry days whose positions have all closed (fewer when there are
    fewer)."""
    days: dict[int, bool] = {}
    for p in book.positions(policy.ACCOUNT, state=None):
        d = p.plan.get("learner_decision")
        if d in decisions:
            day = decisions[d]
            days[day] = days.get(day, True) and p.state == "CLOSED"
    closed = sorted(d for d, done in days.items() if done)[-WINDOW:]
    keep = set(closed)
    return {k: v for k, v in decisions.items() if v in keep}


def review(ledger: sqlite3.Connection, research: sqlite3.Connection, now: datetime) -> dict[str, Any]:
    """Record graduation, demotion or graduation again for the current version (PAPER step only)."""
    from desk.stocks.paper.book import PaperBook

    return step(research, PaperBook(ledger), pr.versions(ledger, research).get(current(), {}), now)


def step(research: sqlite3.Connection, book: Any, decisions: dict[int, int], now: datetime) -> dict[str, Any]:
    """``review`` on a book and the current version's decisions (decision id → session)."""
    key = current()
    if "confirmed" not in pr.stages(research).get(key, {}):
        return {"graduated": False, "why": "not confirmed"}
    history = stages(research, key)
    state = history[-1]["stage"] if history else None

    def record(stage: str, detail: dict[str, Any]) -> None:
        research.execute("INSERT OR IGNORE INTO stk_learn_graduation(model_code, policy_version, at, stage, "
                         "detail_json) VALUES (?,?,?,?,?)", (*key, to_iso(now), stage,
                                                             json.dumps(detail, allow_nan=False, default=str)))

    if state is None:
        record("graduated", {"why": "confirmed by its forward test", "basis": "promotion v2"})
        return {"graduated": True, "changed": "graduated"}
    window = latest_window(book, decisions)
    result = pr.judge(book, window, of.counter(research, "model"))
    detail = {k: result.get(k) for k in ("why", "first", "last", "vs_random", "vs_rules", "deflated")}
    if state == "graduated" and result.get("verdict") == "failed":
        record("demoted", detail)
        return {"graduated": False, "changed": "demoted", **detail}
    if state == "demoted" and result.get("verdict") == "confirmed" and \
            (result.get("first") or "") > str(history[-1].get("last") or ""):
        record("graduated", {**detail, "again": True})
        return {"graduated": True, "changed": "graduated again", **detail}
    return {"graduated": state == "graduated", "changed": None, "window": detail}


def picks(ledger: sqlite3.Connection | None, session: date) -> dict[str, Any]:
    """The Learner's newest decided trades (its "daily picks"): the newest session it traded on or before
    ``session``, at most ``PICK_AGE`` sessions old."""
    from desk.stocks import calendar as cal

    if ledger is None:
        return {"session": None, "items": []}
    try:
        row = ledger.execute("SELECT MAX(session) FROM stk_learner_decisions WHERE account=? AND session<=?",
                             (policy.ACCOUNT, session.toordinal())).fetchone()
    except sqlite3.OperationalError:
        return {"session": None, "items": []}
    if not row or row[0] is None:
        return {"session": None, "items": []}
    day = date.fromordinal(int(row[0]))
    if len(cal.trading_days(day, session)) - 1 > PICK_AGE:
        return {"session": day.isoformat(), "items": [], "stale": True}
    items = []
    for r in ledger.execute("SELECT d.*, p.evidence_json FROM stk_learner_decisions d JOIN stk_decisions p ON "
                            "p.id = d.paper_decision_id WHERE d.account=? AND d.session=? ORDER BY d.id",
                            (policy.ACCOUNT, row[0])):
        ev = json.loads(r["evidence_json"])
        scores = json.loads(r["scores_json"])
        items.append({"symbol": r["symbol"], "key": r["action_key"], "dials": act.parse_key(r["action_key"]).describe(),
                      "mode": r["mode"], "exit_rule": r["exit_rule"], "exit_title": act.BY_EXIT[r["exit_rule"]].title,
                      "reason": r["reason"], "expected": scores.get("expected"), "lcb": scores.get("lcb"),
                      "legs": [{"occ": leg["occ"], "kind": leg["kind"], "strike": leg["strike"],
                                "expiration": leg["expiration"], "side": "buy" if leg["quantity"] > 0 else "sell"}
                               for leg in ev.get("legs") or []]})
    return {"session": day.isoformat(), "items": items}


__all__ = ["NAME", "PICK_AGE", "SECTION", "VERSION", "WINDOW", "content", "current", "latest_window", "picks", "review",
           "stages", "status", "step", "version_hash"]
