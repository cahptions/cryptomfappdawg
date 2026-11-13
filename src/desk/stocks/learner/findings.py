"""What the Learner learned to do, and not to do: written by the system, with evidence (docs/STOCKS_LEARNER.md →
Do / don't findings).

**Held-out data only.** A finding is looked for on a model's walk-forward held-out rows (``model.evaluate``: rows
predicted by models that never trained on them, with their realised, unclipped returns held to expiry), grid
candidates only. Those rows' sessions are cut in two: the earlier ``DISCOVERY_SHARE`` is where rules are looked for,
the rest (after an ``EMBARGO`` of sessions) is kept untouched to confirm them.

**Looking.** For each structure with enough rows, two shallow regression trees (depth ``DEPTH``, large leaves) are fit
on a short list of readable inputs (``RULE_FEATURES``): one on the realised returns, one on the model's held-out
predictions. Each leaf is a rule: the structure plus at most ``DEPTH`` conditions, thresholds rounded to readable
steps. The structure alone and each structure × expiry bucket are rules too.

**Keeping.** A rule is evaluated on its matching rows **by day** (each day's mean is one observation; neighbouring
rows of one day are not independent):

* a **do** needs a positive mean return with a 90% bootstrap range over days above zero;
* a **don't** needs a negative mean return *and* a mean below the same day's average candidate (the excess) with its
  90% bootstrap range below zero: it is worse than picking at random, not only a losing trade;
* at least ``MIN_DAYS`` days and ``MIN_ROWS`` rows;
* its one-sided t-test p-value (on the daily means or excesses) clears **Benjamini-Hochberg** at ``FDR`` across every
  rule evaluated in that run.

It is then a **candidate**. **Confirmed** needs the untouched later days (at least ``CONFIRM_DAYS``) to show the same
direction again: a one-sided p below ``ALPHA_CONFIRM`` on that window alone, its range excluding zero. If those days
do not, it **failed** (kept). Each later training re-checks the candidates still waiting and the confirmed findings on
the newest held-out days after their windows; a confirmed finding whose later mean has turned the other way over at
least ``CONFIRM_DAYS`` days is **retired**. Every stage is a new row; nothing is edited.

A confirmed "don't" is a hard filter for the Learner account (``policy``); a "do" is information, never an order.
``content`` is the frozen rule set; ``version_hash`` its fingerprint.
"""

from __future__ import annotations

import json
import math
import sqlite3
import zlib
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from statistics import fmean
from typing import Any

import numpy as np

from desk.core.clock import to_iso
from desk.core.hashing import canonical_json, sha256_hex
from desk.stocks.learner import actions as act
from desk.stocks.learner import model as md
from desk.stocks.learner import regimes as rg
from desk.stocks.learning_promotion import one_sided_p

NAME = "stocks_learner_findings"
VERSION = 1
DISCOVERY_SHARE = 2 / 3
EMBARGO = md.EMBARGO
DEPTH = 2
MIN_LEAF_SHARE = 0.05
MIN_LEAF = 40
MIN_ROWS = 60
MIN_DAYS = 15
CONFIRM_DAYS = 10
FDR = 0.10
ALPHA_CONFIRM = 0.05
MAX_PER_KIND = 12
OVERLAP = 0.8                       # two rules matching mostly the same rows: only the stronger is kept
CLIP = md.CLIP_HIGH
CATEGORIES: dict[str, tuple[int, ...]] = {"bucket": tuple(range(len(act.DTE_BUCKETS))), "regime_trend": (0, 1),
                                          "regime_vol": (0, 1, 2), "regime_credit": (0, 1)}
# (feature, readable step, words, unit) — the inputs a rule may use, each easy to say in plain words
RULE_FEATURES: tuple[tuple[str, float, str, str], ...] = (
    ("bucket", 1, "expiry", "bucket"), ("target_delta", 0.05, "|delta|", "x"), ("dte", 1, "days to expiry", "n"),
    ("moneyness", 0.01, "strike against the price", "pct"), ("iv_rank", 5, "IV rank", "n"),
    ("iv_rv", 0.05, "IV against realized volatility", "times"), ("desk_momentum", 5, "momentum score", "n"),
    ("committee", 5, "committee score", "n"), ("ret_21d", 0.01, "1-month return", "pct"),
    ("ret_63d", 0.01, "3-month return", "pct"), ("above_sma200", 0.01, "price against its 200-day average", "pct"),
    ("earnings_days", 1, "days to results", "n"), ("skew_25d", 0.01, "put skew", "x"),
    ("spread_pct", 0.01, "widest leg spread", "pct"), ("regime_trend", 1, "market trend", "regime"),
    ("regime_vol", 1, "market volatility", "regime"), ("regime_credit", 1, "credit", "regime"))
RULE_NAMES = tuple(f[0] for f in RULE_FEATURES)
BY_FEATURE = {f[0]: f for f in RULE_FEATURES}


def content() -> dict[str, Any]:
    return {"version": VERSION, "discovery_share": round(DISCOVERY_SHARE, 6), "embargo": EMBARGO, "depth": DEPTH,
            "min_leaf_share": MIN_LEAF_SHARE, "min_leaf": MIN_LEAF, "min_rows": MIN_ROWS, "min_days": MIN_DAYS,
            "confirm_days": CONFIRM_DAYS, "fdr": FDR, "alpha_confirm": ALPHA_CONFIRM, "max_per_kind": MAX_PER_KIND,
            "overlap": OVERLAP, "clip": CLIP, "rule_features": [list(f) for f in RULE_FEATURES],
            "test": "one-sided t on daily means (do) or daily excess over the day's average candidate (don't); "
                    "Benjamini-Hochberg across the run; 90% bootstrap range over days must exclude zero",
            "retire": "a confirmed finding whose later held-out mean is on the other side of zero over the "
                      "confirmation days"}


def version_hash() -> str:
    return sha256_hex(canonical_json(content()))[:16]


# ── held-out rows (stored with each model) ───────────────────────────────────────────────────────
def pack_heldout(ds: md.Dataset, held: Mapping[str, np.ndarray]) -> tuple[bytes, dict[str, Any]]:
    """The tested rows' identity, predictions and realised return held to expiry, zlib-packed."""
    rows = np.where(held["tested"])[0]
    regimes = md.regime_labels(ds.subset(rows)) if len(rows) else []

    def col(name: str) -> list[float | None]:
        v = held.get(name)
        if v is None:
            return [None] * len(rows)
        return [None if not math.isfinite(float(x)) else round(float(x), 5) for x in v[rows].tolist()]

    value = {"v": 1, "source": ds.source[rows].tolist(), "session": ds.session[rows].tolist(),
             "symbol": [ds.symbol[i] for i in rows], "index": ds.index[rows].tolist() if len(ds.index) else [],
             "key": [ds.key[i] for i in rows], "origin": [ds.origin[i] for i in rows],
             "explore": ds.explore[rows].tolist(), "regime": regimes,
             "realised": [None if not math.isfinite(float(x)) else round(float(x), 5) for x in ds.y["exp"][rows].tolist()],
             **{name: col(name) for name in ("expected", "q10", "q50", "q90", "p_exp", "best_exit")}}
    sessions = ds.session[rows]
    meta = {"rows": len(rows), "days": len(np.unique(sessions)),
            "first": int(sessions.min()) if len(rows) else None, "last": int(sessions.max()) if len(rows) else None}
    return zlib.compress(json.dumps(value, allow_nan=False).encode(), 6), meta


def unpack_heldout(blob: bytes) -> dict[str, list[Any]]:
    value: dict[str, list[Any]] = json.loads(zlib.decompress(blob))
    return value


def save_heldout(conn: sqlite3.Connection, model_id: str, ds: md.Dataset, held: Mapping[str, np.ndarray],
                 now: datetime) -> dict[str, Any]:
    blob, meta = pack_heldout(ds, held)
    conn.execute("INSERT OR IGNORE INTO stk_learn_heldout(model_id, rows, days, first_session, last_session, rows_zlib, "
                 "recorded_at) VALUES (?,?,?,?,?,?,?)", (model_id, meta["rows"], meta["days"], meta["first"],
                                                         meta["last"], blob, to_iso(now)))
    return {**meta, "bytes": len(blob)}


# ── rules ────────────────────────────────────────────────────────────────────────────────────────
def _step(value: float, step: float) -> float:
    return round(round(value / step) * step, 6)


def matches(rule: Mapping[str, Any], X: np.ndarray) -> np.ndarray:
    """Rows of ``X`` (rows × ``model.FEATURES``) the rule describes. A missing input never matches a condition."""
    ok: np.ndarray = X[:, md.FEATURES.index("structure")] == float(rule["structure"])
    for c in rule["conditions"]:
        x = X[:, md.FEATURES.index(c["f"])]
        present = ~np.isnan(x)
        if "in" in c:
            ok &= present & np.isin(x, np.asarray(c["in"], dtype=np.float32))
            continue
        if c.get("gt") is not None:
            ok &= present & (x > c["gt"])
        if c.get("le") is not None:
            ok &= present & (x <= c["le"])
    return ok


def rule_id(rule: Mapping[str, Any]) -> str:
    return sha256_hex(canonical_json(rule))[:16]


def _fmt(feature: str, v: float) -> str:
    unit = BY_FEATURE[feature][3]
    if unit == "pct":
        return f"{v * 100:+.0f}%"
    if unit == "times":
        return f"{v:.2f}×"
    if unit == "x":
        return f"{v:.2f}"
    return f"{v:.0f}"


def _words_condition(c: Mapping[str, Any]) -> str:
    feature = c["f"]
    label = BY_FEATURE[feature][2]
    if "in" in c:
        codes = [int(x) for x in c["in"]]
        if feature == "bucket":
            names = [act.DTE_BUCKETS[i][1].lower() for i in codes]
            return "expiring " + " or ".join(f"{n} ({act.DTE_BUCKETS[i][2]}–{act.DTE_BUCKETS[i][3]} days)"
                                             for n, i in zip(names, codes, strict=True))
        if feature == "regime_trend":
            return "in a market " + " or ".join(rg.TREND[i] for i in codes)
        if feature == "regime_vol":
            return "when market volatility is " + " or ".join(rg.VOL[i] for i in codes)
        return "with credit " + " or ".join("widening" if i else "steady" for i in codes)
    lo, hi = c.get("gt"), c.get("le")
    if lo is not None and hi is not None:
        return f"{label} {_fmt(feature, lo)} to {_fmt(feature, hi)}"
    if hi is not None:
        return f"{label} at most {_fmt(feature, hi)}"
    return f"{label} over {_fmt(feature, float(lo or 0.0))}"


def describe(rule: Mapping[str, Any]) -> str:
    """The rule in plain words: "Long calls expiring about a month (22–45 days) with IV rank at most 20"."""
    title = act.STRUCTURES[int(rule["structure"])].title
    words = [title[0].upper() + title[1:].lower() + "s"]
    conds = [_words_condition(c) for c in rule["conditions"]]
    if conds:
        words.append(conds[0] if conds[0].startswith(("expiring", "in a", "when", "with")) else "with " + conds[0])
        words.extend(c if c.startswith(("expiring", "in a", "when", "with")) else "and " + c for c in conds[1:])
    return " ".join(words)


def sentence(kind: str, rule: Mapping[str, Any], ev: Mapping[str, Any], status: str = "candidate") -> str:
    """The finding as written: what, how much, on how many trades and days, and that it is held out."""
    what = describe(rule)
    tail = (f"{ev['rows']:,} candidates over {ev['days']} days, held-out"
            + (f", {ev['first']} to {ev['last']}" if ev.get("first") else ""))
    if kind == "do":
        return (f"Do: {what}: {ev['mean'] * 100:+.0f}% average held to expiry (90% range {ev['low'] * 100:+.0f}% to "
                f"{ev['high'] * 100:+.0f}%), {tail}.")
    return (f"Don't: {what}: {ev['mean'] * 100:+.0f}% average held to expiry, {ev['excess'] * 100:+.0f} points against "
            f"the same day's average candidate (90% range {ev['excess_low'] * 100:+.0f} to "
            f"{ev['excess_high'] * 100:+.0f}), {tail}.")


def _tree_rules(X: np.ndarray, target: np.ndarray, structure: int) -> list[dict[str, Any]]:
    """The leaves of a shallow regression tree, as rules (the structure plus rounded conditions)."""
    from sklearn.tree import DecisionTreeRegressor

    cols = [md.FEATURES.index(f) for f in RULE_NAMES]
    leaf = max(MIN_LEAF, int(MIN_LEAF_SHARE * len(target)))
    tree = DecisionTreeRegressor(max_depth=DEPTH, min_samples_leaf=leaf, random_state=5)
    tree.fit(X[:, cols], target)
    t = tree.tree_
    out: list[dict[str, Any]] = []

    def walk(node: int, bounds: dict[str, list[float | None]]) -> None:
        if t.children_left[node] == -1:
            if bounds:
                out.append(_rule(structure, bounds))
            return
        f = RULE_NAMES[int(t.feature[node])]
        v = _step(float(t.threshold[node]), BY_FEATURE[f][1])
        lo, hi = bounds.get(f, [None, None])
        left = {**bounds, f: [lo, v if hi is None else min(hi, v)]}
        right = {**bounds, f: [v if lo is None else max(lo, v), hi]}
        walk(int(t.children_left[node]), left)
        walk(int(t.children_right[node]), right)

    walk(0, {})
    return out


def _rule(structure: int, bounds: Mapping[str, Sequence[float | None]]) -> dict[str, Any]:
    conditions: list[dict[str, Any]] = []
    for f in RULE_NAMES:                       # a fixed order, so the same rule always hashes the same
        if f not in bounds:
            continue
        lo, hi = bounds[f]
        if f in CATEGORIES:
            codes = [c for c in CATEGORIES[f] if (lo is None or c > lo) and (hi is None or c <= hi)]
            if codes and len(codes) < len(CATEGORIES[f]):
                conditions.append({"f": f, "in": codes})
        elif lo is not None or hi is not None:
            if lo is not None and hi is not None and lo >= hi:
                continue
            conditions.append({"f": f, "gt": lo, "le": hi})
    return {"structure": structure, "conditions": conditions}


def candidate_rules(X: np.ndarray, realised: np.ndarray, predicted: np.ndarray) -> list[dict[str, Any]]:
    """Every rule looked at: each structure, each structure × expiry bucket, and the leaves of two trees per
    structure (on the realised returns and on the model's predictions)."""
    structure = X[:, md.FEATURES.index("structure")]
    bucket = X[:, md.FEATURES.index("bucket")]
    rules: dict[str, dict[str, Any]] = {}
    for s in sorted({int(v) for v in structure[~np.isnan(structure)].tolist()}):
        rows = structure == s
        if rows.sum() < MIN_ROWS:
            continue
        found = [{"structure": s, "conditions": []}]
        for b in sorted({int(v) for v in bucket[rows & ~np.isnan(bucket)].tolist()}):
            found.append({"structure": s, "conditions": [{"f": "bucket", "in": [b]}]})
        y = np.clip(realised[rows], -1.0, CLIP)
        found += _tree_rules(X[rows], y, s)
        p = predicted[rows]
        if np.isfinite(p).sum() >= MIN_ROWS:
            ok = np.isfinite(p)
            found += _tree_rules(X[rows][ok], p[ok], s)
        for r in found:
            rules.setdefault(rule_id(r), r)
    return list(rules.values())


# ── statistics ───────────────────────────────────────────────────────────────────────────────────
def _by_day(sessions: np.ndarray, values: np.ndarray, mask: np.ndarray) -> dict[int, float]:
    by: dict[int, list[float]] = defaultdict(list)
    for s, v in zip(sessions[mask].tolist(), values[mask].tolist(), strict=True):
        by[int(s)].append(float(v))
    return {s: fmean(v) for s, v in by.items()}


def evaluate_rule(mask: np.ndarray, sessions: np.ndarray, realised: np.ndarray,
                  day_average: Mapping[int, float]) -> dict[str, Any]:
    """Rows, days, the mean of daily means with a 90% bootstrap range, the excess over each day's average candidate
    with its range, and the one-sided p-values of both."""
    daily = _by_day(sessions, realised, mask)
    excess = {s: v - day_average[s] for s, v in daily.items() if s in day_average}
    b, e = md.boot(daily), md.boot(excess)
    out: dict[str, Any] = {"rows": int(mask.sum()), "days": len(daily)}
    if b is None or e is None:
        return {**out, "mean": None}
    first, last = min(daily), max(daily)
    return {**out, "mean": b["mean"], "low": b["low"], "high": b["high"], "excess": e["mean"],
            "excess_low": e["low"], "excess_high": e["high"],
            "p_do": round(one_sided_p(list(daily.values())), 6),
            "p_dont": round(one_sided_p([-v for v in excess.values()]), 6),
            "first": date.fromordinal(first).isoformat(), "last": date.fromordinal(last).isoformat(),
            "first_session": first, "last_session": last}


def benjamini_hochberg(pvalues: Mapping[str, float], q: float = FDR) -> tuple[set[str], float]:
    """The hypotheses kept at false-discovery rate ``q``, and the largest p-value kept (0 when none)."""
    ordered = sorted(pvalues.items(), key=lambda kv: (kv[1], kv[0]))
    m = len(ordered)
    cut = 0
    for i, (_, p) in enumerate(ordered, 1):
        if p <= q * i / m:
            cut = i
    kept = {name for name, _ in ordered[:cut]}
    return kept, (ordered[cut - 1][1] if cut else 0.0)


def _direction_holds(kind: str, ev: Mapping[str, Any]) -> bool:
    if ev.get("mean") is None:
        return False
    if kind == "do":
        return bool(ev["mean"] > 0 and ev["low"] > 0 and ev["p_do"] < ALPHA_CONFIRM)
    return bool(ev["mean"] < 0 and ev["excess_high"] < 0 and ev["p_dont"] < ALPHA_CONFIRM)


def _contradicted(kind: str, ev: Mapping[str, Any]) -> bool:
    if ev.get("mean") is None:
        return False
    return bool(ev["mean"] <= 0) if kind == "do" else bool(ev["excess"] >= 0)


def _by_regime(mask: np.ndarray, sessions: np.ndarray, realised: np.ndarray, regimes: np.ndarray
               ) -> list[dict[str, Any]]:
    out = []
    for name in sorted(set(regimes[mask].tolist())):
        rows = mask & (regimes == name)
        daily = _by_day(sessions, realised, rows)
        out.append({"regime": name, "rows": int(rows.sum()), "days": len(daily),
                    "mean": round(fmean(daily.values()), 4) if daily else None})
    return out


# ── the store ────────────────────────────────────────────────────────────────────────────────────
def current(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every finding with its latest stage (newest first)."""
    try:
        rows = conn.execute("SELECT * FROM stk_learn_findings ORDER BY found_at DESC, id").fetchall()
    except sqlite3.OperationalError:
        return []
    stages: dict[str, list[dict[str, Any]]] = defaultdict(list)
    order = {"candidate": 0, "confirmed": 1, "failed": 2, "retired": 3}
    for s in conn.execute("SELECT * FROM stk_learn_finding_status ORDER BY at"):
        stages[s["finding_id"]].append({"status": s["status"], "at": s["at"], "model_id": s["model_id"],
                                        "first_session": s["first_session"], "last_session": s["last_session"],
                                        "evidence": json.loads(s["evidence_json"])})
    out = []
    for r in rows:
        history = sorted(stages.get(r["id"], []), key=lambda x: (x["at"], order[x["status"]]))
        latest = history[-1] if history else {"status": "candidate"}
        rule = json.loads(r["rule_json"])
        out.append({"id": r["id"], "kind": r["kind"], "rule": rule, "words": r["words"], "status": latest["status"],
                    "model_id": r["model_id"], "found_at": r["found_at"], "evidence": json.loads(r["evidence_json"]),
                    "first_session": r["first_session"], "last_session": r["last_session"], "stages": history,
                    "rules_version": r["rules_version"]})
    return out


def confirmed_donts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """The confirmed "don't" findings: the Learner account's hard filter."""
    return [f for f in current(conn) if f["kind"] == "dont" and f["status"] == "confirmed"]


def _stage(conn: sqlite3.Connection, finding: str, status: str, model_id: str, ev: Mapping[str, Any],
           now: datetime) -> bool:
    cur = conn.execute("INSERT OR IGNORE INTO stk_learn_finding_status(finding_id, at, status, model_id, first_session, "
                       "last_session, evidence_json) VALUES (?,?,?,?,?,?,?)",
                       (finding, to_iso(now), status, model_id, ev.get("first_session"), ev.get("last_session"),
                        json.dumps(ev, allow_nan=False)))
    return bool(cur.rowcount)


# ── mining ───────────────────────────────────────────────────────────────────────────────────────
def _later(sessions: np.ndarray, after: int) -> np.ndarray:
    """Rows decided more than ``EMBARGO`` sessions after ``after``."""
    unique = np.unique(sessions)
    pos = int(np.searchsorted(unique, after, side="right")) + EMBARGO
    if pos >= len(unique):
        return np.zeros(len(sessions), bool)
    out: np.ndarray = sessions >= unique[pos]
    return out


def mine(conn: sqlite3.Connection, ds: md.Dataset, held: Mapping[str, np.ndarray], model_id: str,
         now: datetime) -> dict[str, Any]:
    """Re-check the stored findings on the newest held-out days, then look for new ones (see the module docstring)."""
    realised = ds.y["exp"]
    grid = np.array([bool(k) for k in ds.key]) if len(ds) else np.zeros(0, bool)
    base = held["tested"] & ~np.isnan(realised) & grid
    if not base.any():
        return {"looked": False, "reason": "no held-out rows with a realised return"}
    sessions = ds.session
    regimes = np.array(md.regime_labels(ds), dtype=object)
    day_average = _by_day(sessions, realised, base)
    predicted = held.get("expected", np.full(len(ds), np.nan))
    out: dict[str, Any] = {"looked": True, "rows": int(base.sum()), "days": len(day_average), "rechecked": 0,
                           "confirmed": 0, "failed": 0, "retired": 0, "new": 0}
    # 1. the stored findings, on the held-out days after their windows
    for f in current(conn):
        if f["status"] not in ("candidate", "confirmed"):
            continue
        last = max(int(s["last_session"] or 0) for s in f["stages"]) if f["stages"] else int(f["last_session"])
        if f["status"] == "confirmed":
            last = next((int(s["last_session"]) for s in f["stages"] if s["status"] == "confirmed"), last)
        rows = base & _later(sessions, last) & matches(f["rule"], ds.X)
        ev = evaluate_rule(rows, sessions, realised, day_average)
        out["rechecked"] += 1
        if ev["days"] < CONFIRM_DAYS:
            continue
        ev = {**ev, "regimes": _by_regime(rows, sessions, realised, regimes), "test_model": model_id}
        if f["status"] == "candidate":
            done = "confirmed" if _direction_holds(f["kind"], ev) else "failed"
            out[done] += int(_stage(conn, f["id"], done, model_id, ev, now))
        elif _contradicted(f["kind"], ev):
            out["retired"] += int(_stage(conn, f["id"], "retired", model_id, ev, now))
    # 2. new rules, looked for on the earlier part of the held-out days only
    days = np.unique(sessions[base])
    if len(days) < MIN_DAYS:
        return {**out, "reason": f"fewer than {MIN_DAYS} held-out days"}
    cut = int(days[max(0, int(len(days) * DISCOVERY_SHARE) - 1)])
    discovery = base & (sessions <= cut)
    confirm = base & _later(sessions, cut)
    known = {f["id"] for f in current(conn)}
    rules = candidate_rules(ds.X[discovery], realised[discovery], predicted[discovery])
    evaluated: dict[str, tuple[dict[str, Any], str, dict[str, Any], np.ndarray]] = {}
    for rule in rules:
        mask = discovery & matches(rule, ds.X)
        if mask.sum() < MIN_ROWS:
            continue
        ev = evaluate_rule(mask, sessions, realised, day_average)
        if ev.get("mean") is None or ev["days"] < MIN_DAYS:
            continue
        kind = "do" if ev["mean"] > 0 else "dont"
        evaluated[rule_id(rule)] = (rule, kind, ev, mask)
    pvalues = {k: v[2]["p_do" if v[1] == "do" else "p_dont"] for k, v in evaluated.items()}
    kept, threshold = benjamini_hochberg(pvalues)
    out.update(rules_looked=len(rules), rules_tested=len(evaluated), kept_by_fdr=len(kept))
    passing = []
    for k in kept:
        rule, kind, ev, mask = evaluated[k]
        ok = ev["low"] > 0 if kind == "do" else (ev["mean"] < 0 and ev["excess_high"] < 0)
        if ok:
            passing.append((pvalues[k], k, rule, kind, ev, mask))
    chosen: dict[str, list[tuple[str, dict[str, Any], dict[str, Any], np.ndarray, float]]] = {"do": [], "dont": []}
    for p, k, rule, kind, ev, mask in sorted(passing, key=lambda x: (x[0], x[1])):
        overlaps = any((mask & m).sum() / max(1, (mask | m).sum()) > OVERLAP for _, _, _, m, _ in chosen[kind])
        if not overlaps and len(chosen[kind]) < MAX_PER_KIND:
            chosen[kind].append((k, rule, ev, mask, p))
    for kind, items in chosen.items():
        for k, rule, ev, mask, p in items:
            if k in known:
                continue
            sources = {"forward": int((mask & (ds.source == 0)).sum()), "replay": int((mask & (ds.source == 1)).sum())}
            evidence = {**ev, "p": p, "fdr": FDR, "fdr_threshold": threshold, "rules_tested": len(evaluated),
                        "sources": sources, "regimes": _by_regime(mask, sessions, realised, regimes),
                        "basis": "held-out rows (walk-forward), realised returns held to expiry, grid candidates"}
            conn.execute("INSERT OR IGNORE INTO stk_learn_findings(id, kind, rule_json, words, rules_version, model_id, "
                         "evidence_json, first_session, last_session, found_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (k, kind, canonical_json(rule), sentence(kind, rule, ev), version_hash(), model_id,
                          json.dumps(evidence, allow_nan=False), ev["first_session"], ev["last_session"], to_iso(now)))
            _stage(conn, k, "candidate", model_id, evidence, now)
            out["new"] += 1
            later = confirm & matches(rule, ds.X)
            cev = evaluate_rule(later, sessions, realised, day_average)
            if cev["days"] >= CONFIRM_DAYS:
                cev = {**cev, "regimes": _by_regime(later, sessions, realised, regimes), "test_model": model_id}
                done = "confirmed" if _direction_holds(kind, cev) else "failed"
                out[done] += int(_stage(conn, k, done, model_id, cev, now))
    return out


def filter_reason(donts: Iterable[Mapping[str, Any]], X: np.ndarray) -> list[str | None]:
    """For each row of ``X``, the first confirmed "don't" it matches (its id), or None."""
    out: list[str | None] = [None] * X.shape[0]
    for f in donts:
        hit = matches(f["rule"], X)
        for i in np.where(hit)[0]:
            if out[i] is None:
                out[i] = f["id"]
    return out


__all__ = ["ALPHA_CONFIRM", "CONFIRM_DAYS", "FDR", "MIN_DAYS", "NAME", "VERSION", "benjamini_hochberg",
           "candidate_rules", "confirmed_donts", "content", "current", "describe", "evaluate_rule", "filter_reason",
           "matches", "mine", "pack_heldout", "rule_id", "save_heldout", "sentence", "unpack_heldout", "version_hash"]
