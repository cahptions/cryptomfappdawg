"""Promotion of a Learner version only after forward confirmation (docs/STOCKS_LEARNER.md → Promotion).

A **Learner version** is the model code (``model.version_hash``: the features, targets and training procedure the
weekly refits share) together with the policy (``policy.version_hash``). Each weekly model is a refit of the same
version; the version is what is promoted or not. It is **provisional** from the session it first trades.

**The forward test, fixed in advance.** Its first ``FORWARD_DAYS`` entry days (decision sessions on which the Learner
account opened at least one position under that version), once every position decided on them has closed. On those
days only:

* **against Random**: each day's mean realised return on the money at risk of the Learner's trades minus the same
  day's mean of its random twin's trades (days where both traded), averaged over days with a 90% bootstrap range;
* **against the rule-based option strategies**: the mean of the Learner's daily means minus the mean of the rule-based
  PAPER option strategies' daily means over the same calendar window (entered on days in it), the 90% range from
  resampling each side's days. It needs at least ``MIN_RULE_DAYS`` rule entry days; with fewer the version stays
  provisional and says so.

Both ranges above zero: **confirmed**. Otherwise **failed**, kept and shown, and the version is never promoted on
those days again (a new version starts its own test). Returns are realised at real fills only: a position closed at a
modelled price (an underlying split, a modelled exit) is left out of every side. ``content`` is the frozen rule.

This is the Learner's own ledger of honesty, in the style of ``learning_promotion``; it adopts nothing anywhere.
"""

from __future__ import annotations

import json
import random
import sqlite3
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from statistics import fmean
from typing import Any

from desk.core.clock import to_iso
from desk.core.hashing import canonical_json, sha256_hex
from desk.stocks.learner import model as md
from desk.stocks.learner import policy

NAME = "stocks_learner_promotion"
VERSION = 1
FORWARD_DAYS = 20
MIN_RULE_DAYS = 5
DRAWS = 2000


def content() -> dict[str, Any]:
    return {"version": VERSION, "forward_days": FORWARD_DAYS, "min_rule_days": MIN_RULE_DAYS, "draws": DRAWS,
            "unit": "model code version + policy version", "metric": "realised return on the money at risk per "
            "trade, averaged by entry day; real fills only", "vs_random": "paired by day, 90% bootstrap over days",
            "vs_rules": "difference of daily means over the same window, 90% bootstrap resampling each side",
            "confirmed": "both lower ends above zero"}


def version_hash() -> str:
    return sha256_hex(canonical_json(content()))[:16]


def realised(p: Any, evidence: Mapping[str, Any] | None = None) -> float | None:
    """A closed position's realised return on its money at risk, or None when it closed at a modelled price."""
    if p.state != "CLOSED" or p.pnl is None:
        return None
    if (p.exit_reason or "").startswith("underlying split") or (evidence or {}).get("modeled_exit"):
        return None
    risk = p.max_loss if p.max_loss else p.entry_cost + p.entry_fees
    return None if not risk or risk <= 0 else float(p.pnl) / float(risk)


def _evidence(conn: sqlite3.Connection, position_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT exit_evidence_json FROM stk_positions WHERE id=?", (position_id,)).fetchone()
    try:
        value = json.loads(row[0]) if row and row[0] else {}
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def _boot(values: Sequence[float], seed: int = 3) -> dict[str, Any] | None:
    if len(values) < 3:
        return None
    rng = random.Random(seed)       # noqa: S311 - a reproducible resample
    means = sorted(fmean(rng.choices(values, k=len(values))) for _ in range(DRAWS))
    return {"mean": round(fmean(values), 4), "low": round(means[int(0.05 * DRAWS)], 4),
            "high": round(means[int(0.95 * DRAWS) - 1], 4), "days": len(values)}


def _boot_difference(a: Sequence[float], b: Sequence[float], seed: int = 5) -> dict[str, Any] | None:
    if len(a) < 3 or len(b) < 3:
        return None
    rng = random.Random(seed)       # noqa: S311
    diffs = sorted(fmean(rng.choices(a, k=len(a))) - fmean(rng.choices(b, k=len(b))) for _ in range(DRAWS))
    return {"mean": round(fmean(a) - fmean(b), 4), "low": round(diffs[int(0.05 * DRAWS)], 4),
            "high": round(diffs[int(0.95 * DRAWS) - 1], 4), "days": len(a), "other_days": len(b)}


def rule_strategies() -> list[str]:
    """The rule-based PAPER option strategies (not baselines, not the Learner's accounts)."""
    from desk.stocks.paper.strategies import STRATEGIES

    return [s.id for s in STRATEGIES if s.asset == "option" and not s.baseline]


def daily(book: Any, account: str, *, decided_by: Mapping[int, int] | None = None, sessions: set[int] | None = None,
          window: tuple[date, date] | None = None) -> tuple[dict[int, float], int, int]:
    """(entry day → mean realised return, closed trades counted, trades still open) of an account. A Learner account's
    day is its decision session (``plan.decided``); another account's is its entry day."""
    by: dict[int, list[float]] = defaultdict(list)
    still_open = 0
    counted = 0
    for p in book.positions(account, state=None):
        day = date.fromisoformat(p.plan["decided"]).toordinal() if p.plan.get("decided") else p.entry_day.toordinal()
        if sessions is not None and day not in sessions:
            continue
        if window is not None and not window[0].toordinal() <= day <= window[1].toordinal():
            continue
        if decided_by is not None and p.plan.get("learner_decision") not in decided_by:
            continue
        if p.state == "OPEN":
            still_open += 1
            continue
        r = realised(p, _evidence(book.conn, p.id))
        if r is not None:
            by[day].append(r)
            counted += 1
    return {d: fmean(v) for d, v in by.items()}, counted, still_open


def versions(ledger: sqlite3.Connection, research: sqlite3.Connection) -> dict[tuple[str, str], dict[int, int]]:
    """(model code, policy version) → {learner decision id: decision session} of the Learner's trades."""
    codes = {r[0]: r[1] for r in research.execute("SELECT id, code_version FROM stk_learn_models")}
    out: dict[tuple[str, str], dict[int, int]] = defaultdict(dict)
    for r in ledger.execute("SELECT id, session, model_id, policy_version FROM stk_learner_decisions WHERE account=? "
                            "AND model_id IS NOT NULL ORDER BY session, id", (policy.ACCOUNT,)):
        code = codes.get(r[2])
        if code is not None:
            out[(code, r[3])][int(r[0])] = int(r[1])
    return dict(out)


def stages(research: sqlite3.Connection) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = defaultdict(dict)
    try:
        for r in research.execute("SELECT * FROM stk_learn_promotions ORDER BY at"):
            out[(r["model_code"], r["policy_version"])][r["stage"]] = {"at": r["at"], **json.loads(r["detail_json"])}
    except sqlite3.OperationalError:
        pass
    return dict(out)


def _record(research: sqlite3.Connection, code: str, pv: str, stage: str, detail: Mapping[str, Any],
            now: datetime) -> None:
    research.execute("INSERT OR IGNORE INTO stk_learn_promotions(model_code, policy_version, stage, at, detail_json) "
                     "VALUES (?,?,?,?,?)", (code, pv, stage, to_iso(now), json.dumps(detail, allow_nan=False)))


def judge(book: Any, decisions: Mapping[int, int]) -> dict[str, Any]:
    """The forward test of one version (see the module docstring): progress, or the verdict and its evidence."""
    opened = {p.plan.get("learner_decision") for p in book.positions(policy.ACCOUNT, state=None)}
    entry_days = sorted({session for decision, session in decisions.items() if decision in opened})
    window = entry_days[:FORWARD_DAYS]
    out: dict[str, Any] = {"entry_days": len(entry_days), "need": FORWARD_DAYS,
                           "first": date.fromordinal(window[0]).isoformat() if window else None,
                           "last": date.fromordinal(window[-1]).isoformat() if window else None}
    if len(window) < FORWARD_DAYS:
        return {**out, "verdict": None, "why": f"{len(entry_days)} of {FORWARD_DAYS} forward entry days so far"}
    chosen = set(window)
    mine, counted, still_open = daily(book, policy.ACCOUNT, decided_by=decisions, sessions=chosen)
    if still_open:
        return {**out, "verdict": None, "why": f"{still_open} positions from the {FORWARD_DAYS} forward days are "
                                               "still open; judged once all have closed"}
    rand, rand_n, rand_open = daily(book, policy.RANDOM, sessions=chosen)
    if rand_open:
        return {**out, "verdict": None, "why": f"{rand_open} of the random twin's positions from those days are open"}
    paired = [mine[d] - rand[d] for d in sorted(mine) if d in rand]
    first, last = date.fromordinal(window[0]), date.fromordinal(window[-1])
    rules: dict[int, list[float]] = defaultdict(list)
    for sid in rule_strategies():
        got, _, _ = daily(book, sid, window=(first, last))
        for d, v in got.items():
            rules[d].append(v)
    rule_days = [fmean(v) for _, v in sorted(rules.items())]
    vs_random, vs_rules = _boot(paired), _boot_difference(list(mine.values()), rule_days)
    out.update(trades=counted, random_trades=rand_n, paired_days=len(paired), rule_days=len(rule_days),
               learner=_boot(list(mine.values())), random=_boot([rand[d] for d in sorted(rand)]),
               vs_random=vs_random, vs_rules=vs_rules)
    if len(rule_days) < MIN_RULE_DAYS:
        return {**out, "verdict": None, "why": f"the rule-based option strategies entered on {len(rule_days)} days in "
                                               f"the window ({MIN_RULE_DAYS} needed to compare); it stays provisional"}
    ok = bool(vs_random and vs_random["low"] > 0 and vs_rules and vs_rules["low"] > 0)
    return {**out, "verdict": "confirmed" if ok else "failed",
            "why": "beat Random and the rule-based strategies on its untouched forward days, both ranges above zero"
            if ok else "did not beat both Random and the rule-based strategies with ranges above zero"}


def review(ledger: sqlite3.Connection, research: sqlite3.Connection, now: datetime) -> dict[str, Any]:
    """Record each version's provisional stage when it first trades, and its verdict once its forward days are in."""
    from desk.stocks.paper.book import PaperBook

    book = PaperBook(ledger)
    done = stages(research)
    out = {}
    for (code, pv), decisions in versions(ledger, research).items():
        have = done.get((code, pv), {})
        if "provisional" not in have:
            first = min(decisions.values())
            _record(research, code, pv, "provisional", {"first_session": date.fromordinal(first).isoformat(),
                                                        "rule": content()}, now)
        result = judge(book, decisions)
        if result["verdict"] and result["verdict"] not in have:
            _record(research, code, pv, result["verdict"], result, now)
        out[f"{code}/{pv}"] = result
    return out


def overview(ledger: sqlite3.Connection | None, research: sqlite3.Connection) -> list[dict[str, Any]]:
    """Each Learner version: its stage, its forward progress or verdict, and its evidence (read-only)."""
    done = stages(research)
    out = []
    current = (md.version_hash(), policy.version_hash())
    progress: dict[tuple[str, str], dict[str, Any]] = {}
    if ledger is not None:
        from desk.stocks.paper.book import PaperBook

        try:
            for key, decisions in versions(ledger, research).items():
                progress[key] = judge(PaperBook(ledger), decisions)
        except sqlite3.OperationalError:
            progress = {}
    for key in sorted(set(done) | set(progress) | {current}):
        have = done.get(key, {})
        stage = "confirmed" if "confirmed" in have else "failed" if "failed" in have else \
            "provisional" if "provisional" in have else "not traded yet"
        out.append({"model_code": key[0], "policy_version": key[1], "current": key == current, "stage": stage,
                    "stages": have, "forward": progress.get(key)})
    return out


__all__ = ["FORWARD_DAYS", "MIN_RULE_DAYS", "NAME", "VERSION", "content", "daily", "judge", "overview", "realised",
           "review", "rule_strategies", "stages", "version_hash", "versions"]
