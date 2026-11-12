"""Stricter Learning promotion (the 2026-11-13 rules; docs/STOCKS_LEARNING.md → Learning and check-ins).

Before these rules a structure was "promoted" when its held-out day means cleared an approximate
mean-minus-three-standard-errors hurdle, with no correction for how many things were compared and no confirmation.
That was exploratory. Now a recipe becomes a **finding** only in three steps:

1. **A frozen manifest.** When a recipe is registered its manifest (its exact configuration, the hypothesis, the
   metric, the test, the correction and the confirmation rule) is frozen with a hash in ``stk_learning_manifests``.
   Recipes that existed before these rules are frozen when the rules arrive and marked ``backfilled``.
2. **Provisional, after a multiple-comparison correction.** A recipe's held-out day means (each entry day's mean of
   its underlying/day block means, on entry days in the later, held-out half and after any earlier failed
   confirmation) need at least ``MIN_DAYS`` days. Its one-sided t-test p-value (mean above zero) must clear **Holm's
   step-down correction across every active recipe** (a recipe without enough days counts as p = 1), at family
   ``ALPHA``. Passing makes it *provisional*, not a finding.
3. **Confirmed on untouched days.** The confirmation sample is fixed in advance at that moment: the recipe's next
   ``CONFIRM_DAYS`` entry days *after* the promotion day, once every position entered on them has finished. Those
   days were never part of the decision. Their day means must again show a mean above zero at one-sided ``ALPHA``
   (one pre-specified test, no correction). Pass: **confirmed**, the only stage called a finding. Fail: **failed
   confirmation**, kept and shown; the recipe may be promoted again only on days after that window.

Every decision is appended to ``stk_learning_promotions``. Promotions made before these rules are kept and labelled
"provisional (before 2026-11-13 rules)"; they no longer give their structure priority in the small accounts, which
follow a *confirmed* finding only, and still use only research outcomes matured before each of their decisions.

Re-testing a growing sample at every check-in is itself a form of repeated looking, which is why step 3 exists and
why nothing short of it is called a finding. None of this is a guarantee of future profit.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, time
from statistics import fmean, stdev
from typing import Any

from desk.core.clock import to_iso
from desk.core.hashing import canonical_json, sha256_hex
from desk.stocks import calendar as cal

RULES = "2026-11-13"
LEGACY_LABEL = "provisional (before 2026-11-13 rules)"
ALPHA = 0.05
MIN_DAYS = 20
CONFIRM_DAYS = 20
RESEARCH = "uncapped_research"


# ── statistics ───────────────────────────────────────────────────────────────────────────────────
def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the regularized incomplete beta (Numerical Recipes, modified Lentz)."""
    tiny, qab, qap, qam = 1e-300, a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c if abs(1.0 + aa / c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c if abs(1.0 + aa / c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-12:
            break
    return h


def _betainc(a: float, b: float, x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1) / (a + b + 2):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_sf(t: float, df: int) -> float:
    """P(T > t) for Student's t with ``df`` degrees of freedom."""
    if math.isnan(t):
        return 1.0
    if math.isinf(t):
        return 0.0 if t > 0 else 1.0
    tail = 0.5 * _betainc(df / 2.0, 0.5, df / (df + t * t))
    return tail if t > 0 else 1.0 - tail


def one_sided_p(values: Sequence[float]) -> float:
    """One-sided p-value that the mean is above zero (t-test on the values, each one entry day)."""
    if len(values) < 2:
        return 1.0
    sd = stdev(values)
    if sd == 0:
        return 0.0 if fmean(values) > 0 else 1.0
    return t_sf(fmean(values) / (sd / math.sqrt(len(values))), len(values) - 1)


def holm(pvalues: Mapping[str, float], alpha: float = ALPHA) -> dict[str, float]:
    """Holm's step-down procedure: the hypotheses rejected, each with the threshold it cleared."""
    ordered = sorted(pvalues.items(), key=lambda kv: (kv[1], kv[0]))
    m = len(ordered)
    out = {}
    for i, (name, p) in enumerate(ordered):
        threshold = alpha / (m - i)
        if p > threshold:
            break
        out[name] = threshold
    return out


# ── manifests ────────────────────────────────────────────────────────────────────────────────────
def manifest(experiment_id: str, spec_sha256: str, config: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "rules": RULES, "experiment_id": experiment_id, "spec_sha256": spec_sha256, "config": dict(config),
        "hypothesis": "The recipe's mean net return per entry day, after costs, is above zero",
        "metric": "net return per position, averaged within underlying/day blocks, then within each entry day",
        "sample": f"entry days in the held-out (later) half, after any failed confirmation; at least {MIN_DAYS}",
        "test": f"one-sided t-test on entry-day means; Holm across every active recipe, family alpha {ALPHA}",
        "confirmation": f"the next {CONFIRM_DAYS} entry days after the promotion day, once all their positions have "
                        f"finished; one-sided t-test at {ALPHA}, no reuse of the promotion's days",
    }


def freeze(conn: sqlite3.Connection, experiment_id: str, now: datetime, *, backfilled: bool = False) -> str | None:
    """Freeze a registered recipe's manifest (once). Returns its hash."""
    row = conn.execute("SELECT spec_sha256, config_json FROM stk_learning_experiments WHERE id = ?",
                       (experiment_id,)).fetchone()
    if row is None:
        return None
    found = conn.execute("SELECT manifest_sha256 FROM stk_learning_manifests WHERE experiment_id = ?",
                         (experiment_id,)).fetchone()
    if found:
        return str(found[0])
    value = manifest(experiment_id, row[0], json.loads(row[1]))
    digest = sha256_hex(canonical_json(value))
    conn.execute("INSERT INTO stk_learning_manifests VALUES (?, ?, ?, ?, ?)",
                 (experiment_id, to_iso(now), digest, canonical_json(value), int(backfilled)))
    return digest


def backfill(conn: sqlite3.Connection, now: datetime) -> int:
    """Freeze manifests for recipes registered before these rules (marked as such)."""
    missing = [r[0] for r in conn.execute("SELECT id FROM stk_learning_experiments WHERE id NOT IN "
                                          "(SELECT experiment_id FROM stk_learning_manifests) ORDER BY id")]
    for ident in missing:
        freeze(conn, ident, now, backfilled=True)
    return len(missing)


# ── decisions ────────────────────────────────────────────────────────────────────────────────────
def _record(conn: sqlite3.Connection, experiment_id: str, now: datetime, stage: str, detail: dict[str, Any],
            rules: str = RULES) -> None:
    digest = conn.execute("SELECT manifest_sha256 FROM stk_learning_manifests WHERE experiment_id = ?",
                          (experiment_id,)).fetchone()
    conn.execute("INSERT INTO stk_learning_promotions(experiment_id, at, rules, stage, manifest_sha256, detail_json) "
                 "VALUES (?, ?, ?, ?, ?, ?)", (experiment_id, to_iso(now), rules, stage,
                                               digest[0] if digest else None, canonical_json(detail)))


def relabel_legacy(conn: sqlite3.Connection, model: Mapping[str, Any], last_day: str | None, now: datetime) -> bool:
    """Keep a promotion made before these rules in the record, labelled provisional (once)."""
    champion = model.get("champion")
    if not champion or model.get("rules") == RULES:            # nothing earlier, or already carried over
        return False
    key = f"structure:{champion}"
    if conn.execute("SELECT 1 FROM stk_learning_promotions WHERE experiment_id = ? AND stage = 'legacy'",
                    (key,)).fetchone():
        return False
    _record(conn, key, now, "legacy", {"structure": champion, "label": LEGACY_LABEL, "promoted_through": last_day,
                                       "test_net_return": model.get("test_net_return"),
                                       "test_blocks": model.get("test_blocks"),
                                       "rule": "mean minus three standard errors over held-out day means; no "
                                               "multiple-comparison correction or confirmation"},
            rules="before 2026-11-13")
    return True


def _day(row: Any) -> str:
    return cal.new_york(datetime.fromisoformat(row["entered_at"])).date().isoformat()


def day_means(rows: Iterable[Any]) -> dict[str, float]:
    """Entry day → mean of that day's underlying/day block means (rows are already one per block)."""
    by: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        by[_day(r)].append(float(r["net_return"]))
    return {d: fmean(v) for d, v in sorted(by.items())}


def stages(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    """Each recipe's latest decision."""
    out: dict[str, sqlite3.Row] = {}
    for r in conn.execute("SELECT * FROM stk_learning_promotions WHERE stage != 'legacy' ORDER BY id"):
        out[r["experiment_id"]] = r
    return out


def _open_before(conn: sqlite3.Connection, experiment_id: str, last_day: str) -> int:
    end = datetime.combine(date.fromisoformat(last_day), time(23, 59, 59), cal.NEW_YORK)
    return int(conn.execute(
        "SELECT COUNT(*) FROM stk_positions p JOIN stk_learning_fills f ON f.position_id = p.id "
        "JOIN stk_learning_intents i ON i.order_id = f.order_id WHERE i.experiment_id = ? AND p.account = ? "
        "AND p.state = 'OPEN' AND p.opened_at <= ?", (experiment_id, RESEARCH, to_iso(end))).fetchone()[0])


def decide(conn: sqlite3.Connection, rows: Sequence[Any], boundary: str | None, now: datetime,
           active: Sequence[str]) -> dict[str, Any]:
    """Confirm or fail provisional recipes whose window is complete, then promote new ones that clear Holm.

    ``rows``: matured research outcome rows (one per recipe and block, ``learning.outcome_rows``); ``boundary``: the
    first held-out entry day; ``active``: recipes still running (retired ones are not promoted). Returns what changed.
    """
    by_recipe: dict[str, list[Any]] = defaultdict(list)
    for r in rows:
        by_recipe[r["experiment_id"]].append(r)
    latest = stages(conn)
    changed: dict[str, list[str]] = {"provisional": [], "confirmed": [], "failed": []}
    # 1. confirmations fixed in advance
    for ident, last in latest.items():
        if last["stage"] != "provisional":
            continue
        detail = json.loads(last["detail_json"])
        after = detail["promotion_day"]
        days = day_means(r for r in by_recipe.get(ident, []) if _day(r) > after)
        window = list(days)[:CONFIRM_DAYS]
        if len(window) < CONFIRM_DAYS or _open_before(conn, ident, window[-1]):
            continue
        values = [days[d] for d in window]
        p = one_sided_p(values)
        stage = "confirmed" if p < ALPHA and fmean(values) > 0 else "failed"
        _record(conn, ident, now, stage, {"promotion_id": last["id"], "window": [window[0], window[-1]],
                                          "days": len(values), "mean": fmean(values), "p": p, "alpha": ALPHA,
                                          "fresh_after": window[-1]})
        changed[stage].append(ident)
    # 2. new provisional promotions, Holm-corrected across every active recipe
    latest = stages(conn)
    candidates: dict[str, float] = {}
    samples: dict[str, dict[str, float]] = {}
    for ident in active:
        prior = latest.get(ident)
        if prior is not None and prior["stage"] in ("provisional", "confirmed"):
            continue
        fresh = json.loads(prior["detail_json"]).get("fresh_after", "") if prior is not None else ""
        days = day_means(r for r in by_recipe.get(ident, []) if boundary and _day(r) >= boundary and _day(r) > fresh)
        samples[ident] = days
        candidates[ident] = one_sided_p(list(days.values())) if len(days) >= MIN_DAYS else 1.0
    family = {i: candidates.get(i, 1.0) for i in active}          # recipes under confirmation count, at p = 1
    for ident, threshold in holm(family).items():
        if ident not in candidates:
            continue
        days = samples[ident]
        values = list(days.values())
        if fmean(values) <= 0:
            continue
        _record(conn, ident, now, "provisional", {
            "promotion_day": max(days), "days": len(values), "first_day": min(days), "mean": fmean(values),
            "p": candidates[ident], "holm_threshold": threshold, "recipes_compared": len(family), "alpha": ALPHA,
            "confirmation": {"entry_days": CONFIRM_DAYS, "after": max(days)}})
        changed["provisional"].append(ident)
    return changed


def champion(conn: sqlite3.Connection) -> str | None:
    """The structure of the most recently confirmed recipe still confirmed (its latest decision), if any."""
    confirmed = [r for r in stages(conn).values() if r["stage"] == "confirmed"]
    if not confirmed:
        return None
    best = max(confirmed, key=lambda r: r["id"])
    row = conn.execute("SELECT config_json FROM stk_learning_experiments WHERE id = ?",
                       (best["experiment_id"],)).fetchone()
    return str(json.loads(row[0])["structure"]) if row else None


def overview(conn: sqlite3.Connection, now: datetime) -> dict[str, Any]:
    """Every promotion decision for the Learning page and report: findings, provisional, failed, earlier rules."""
    titles = {r[0]: (json.loads(r[1]).get("label"), json.loads(r[1])["structure"]) for r in
              conn.execute("SELECT id, config_json FROM stk_learning_experiments")}
    progress: dict[str, int] = {}
    out: dict[str, list[dict[str, Any]]] = {"confirmed": [], "provisional": [], "failed": [], "legacy": []}
    latest = stages(conn)
    for r in conn.execute("SELECT * FROM stk_learning_promotions WHERE at <= ? ORDER BY id", (to_iso(now),)):
        detail = json.loads(r["detail_json"])
        label, structure = titles.get(r["experiment_id"], (None, detail.get("structure")))
        item = {"id": r["id"], "experiment_id": r["experiment_id"], "at": r["at"], "rules": r["rules"],
                "stage": r["stage"], "manifest_sha256": r["manifest_sha256"], "structure": structure,
                "recipe": label or (structure or "").replace("_", " "), **detail}
        if r["stage"] == "provisional":
            if latest.get(r["experiment_id"]) is not None and latest[r["experiment_id"]]["id"] != r["id"]:
                continue                  # decided since: shown under confirmed or failed with its promotion id
            if r["experiment_id"] not in progress:
                progress[r["experiment_id"]] = _confirm_progress(conn, r["experiment_id"], detail["promotion_day"],
                                                                 now)
            item["confirmation_days"] = min(progress[r["experiment_id"]], CONFIRM_DAYS)
        out[r["stage"]].append(item)
    manifests = conn.execute("SELECT COUNT(*), SUM(backfilled) FROM stk_learning_manifests").fetchone()
    return {"rules": RULES, "alpha": ALPHA, "min_days": MIN_DAYS, "confirm_days": CONFIRM_DAYS,
            "legacy_label": LEGACY_LABEL, "manifests": int(manifests[0] or 0),
            "backfilled_manifests": int(manifests[1] or 0), **out}


def _confirm_progress(conn: sqlite3.Connection, experiment_id: str, after: str, now: datetime) -> int:
    days = {cal.new_york(datetime.fromisoformat(r[0])).date().isoformat() for r in conn.execute(
        "SELECT entered_at FROM stk_learning_outcomes WHERE experiment_id = ? AND matured_at <= ?",
        (experiment_id, to_iso(now)))}
    return len([d for d in days if d > after])


__all__ = ["ALPHA", "CONFIRM_DAYS", "LEGACY_LABEL", "MIN_DAYS", "RULES", "backfill", "champion", "day_means",
           "decide", "freeze", "holm", "manifest", "one_sided_p", "overview", "relabel_legacy", "t_sf"]
