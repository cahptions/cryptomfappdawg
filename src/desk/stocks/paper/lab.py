"""The learning lab: new stock strategies from combinations of the desks, tested on the rebuilt months before any is
allowed to PAPER trade (docs/STOCKS_PAPER.md).

Every combination of one, two or three of six desks (valuation, quality, growth, momentum, earnings, risk), holding
the top 10 or 20 by their average score, is scored on every rebuilt month: bought at the open after the month's last
session and held to the open after the next month's, minus a 0.2% round trip, against SPY. Picking the best
combination is itself tested walk-forward: at each month the lab picks the combination with the best record over
months whose returns were complete by that day, and that pick's next month is recorded. Only if those picks beat the
committee's own top 10 over at least 12 unseen months (one-sided p < 0.20) does the lab's current best combination
become a PAPER strategy. At most three lab strategies trade at once; a newer one retires the oldest (it stops opening
positions and lets its open ones exit).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
from typing import Any

import numpy as np

from desk.core.clock import to_iso
from desk.core.hashing import canonical_json, sha256_hex
from desk.stocks import desks as dk
from desk.stocks import validation as val
from desk.stocks.paper.strategies import Strategy
from desk.stocks.prices import Panel
from desk.stocks.repo import StocksResearch

LAB_DESKS = ("valuation", "quality", "growth", "momentum", "earnings", "risk")
SIZES = (10, 20)
MIN_TRAIN = 12
MIN_OOS = 12
MAX_ACTIVE = 3
MAX_P = 0.20               # PAPER is where a strategy proves itself; this only keeps luck out of the door
PARENT = "committee/10"


@dataclass(frozen=True)
class Variant:
    desks: tuple[str, ...]
    n: int

    @property
    def key(self) -> str:
        return f"{'+'.join(self.desks)}/{self.n}"


def variants() -> list[Variant]:
    return [Variant(combo, n) for k in (1, 2, 3) for combo in combinations(LAB_DESKS, k) for n in SIZES]


def picks(month: val.Month, desks: Sequence[str], n: int) -> list[str]:
    """The top ``n`` by the average of ``desks`` (names missing any of them are left out)."""
    scored = []
    for s in month.sectors:
        values = [month.scores.get(d, {}).get(s) for d in desks]
        if all(v is not None for v in values):
            scored.append((float(np.mean([v[0] for v in values if v is not None])), s))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [s for _, s in scored[:n]]


def committee_picks(month: val.Month, n: int = 10) -> list[str]:
    scores = val.committee_scores(month, dict(dk.DEFAULT_WEIGHTS))
    return [s for s, _ in sorted(scores.items(), key=lambda x: (-x[1], x[0]))[:n]]


def monthly_excess(fw: val.Forward, months: Sequence[val.Month],
                   chosen: dict[str, list[list[str]]]) -> dict[str, list[float | None]]:
    """For each key, month k's return (open after month k to open after month k+1) minus SPY minus costs."""
    out: dict[str, list[float | None]] = {k: [] for k in chosen}
    spy_i = fw.panel.index.get("SPY")
    for k in range(len(months)):
        r = fw.span(months[k].asof, months[k + 1].asof) if k + 1 < len(months) else None
        spy = None if r is None or spy_i is None or np.isnan(r[spy_i]) else float(r[spy_i])
        for key, per_month in chosen.items():
            if r is None or spy is None:
                out[key].append(None)
                continue
            vals = [float(r[i]) for s in per_month[k] if (i := fw.panel.index.get(s)) is not None and not np.isnan(r[i])]
            out[key].append(None if not vals else float(np.mean(vals)) - spy - val.COST_ROUND_TRIP)
    return out


def evaluate(fw: val.Forward, months: Sequence[val.Month]) -> dict[str, Any]:
    vs = variants()
    chosen = {v.key: [picks(m, v.desks, v.n) for m in months] for v in vs}
    chosen[PARENT] = [committee_picks(m) for m in months]
    excess = monthly_excess(fw, months, chosen)
    walk: list[dict[str, Any]] = []
    for k in range(len(months)):
        known = range(0, k - 1)                     # month j's return is complete at the first session of month j+2
        if len(known) < MIN_TRAIN or excess[PARENT][k] is None:
            continue
        records = {v.key: [x for j in known if (x := excess[v.key][j]) is not None] for v in vs}
        best = max((key for key, xs in records.items() if len(xs) >= MIN_TRAIN), key=lambda key: (np.mean(records[key]), key),
                   default=None)
        if best is None or excess[best][k] is None:
            continue
        walk.append({"asof": months[k].asof.isoformat(), "pick": best, "pick_excess": excess[best][k],
                     "committee_excess": excess[PARENT][k], "train_months": len(known)})
    diffs = np.array([w["pick_excess"] - w["committee_excess"] for w in walk])
    complete = {key: [x for x in xs if x is not None] for key, xs in excess.items()}
    ranked = sorted(((float(np.mean(xs)), key) for key, xs in complete.items() if key != PARENT and len(xs) >= MIN_TRAIN),
                    reverse=True)
    best_now = ranked[0][1] if ranked else None
    p_value = val.one_sided_p(diffs) if len(diffs) else None
    passed = (len(walk) >= MIN_OOS and float(diffs.mean()) > 0 and float(np.mean([w["pick_excess"] for w in walk])) > 0
              and p_value is not None and p_value < MAX_P)
    if not ranked:
        have = max((len(x) for x in complete.values()), default=0)
        why = f"Not enough rebuilt months with complete returns ({have} of {MIN_TRAIN})"
    elif len(walk) < MIN_OOS:
        why = f"Too few unseen months to judge the lab's picks ({len(walk)} of {MIN_OOS})"
    elif passed:
        why = (f"Over {len(walk)} unseen months the lab's picks beat the committee's top 10 by "
               f"{diffs.mean():+.2%} a month (p = {p_value:.2f}) and SPY by {np.mean([w['pick_excess'] for w in walk]):+.2%}")
    else:
        why = (f"Over {len(walk)} unseen months the lab's picks did not clearly beat both the committee "
               f"({diffs.mean():+.2%} a month, p = {'—' if p_value is None else f'{p_value:.2f}'}) and SPY; "
               "nothing is promoted")
    return {"variants": len(vs), "months": len(months), "walk_forward": walk, "oos_months": len(walk),
            "oos_vs_committee": None if not len(diffs) else float(diffs.mean()),
            "p_vs_committee": p_value, "passed": passed, "why": why,
            "best_now": best_now, "leaderboard": [{"key": k, "mean_excess": m, "months": len(complete[k])}
                                                  for m, k in ranked[:15]],
            "committee": {"mean_excess": None if not complete[PARENT] else float(np.mean(complete[PARENT])),
                          "months": len(complete[PARENT])}}


def as_strategy(key: str, evidence: dict[str, Any]) -> Strategy:
    desks_part, n = key.split("/")
    desks = tuple(desks_part.split("+"))
    sid = "lab_" + sha256_hex(key)[:8]
    return Strategy(sid, 1, f"Lab: {' + '.join(d.title() for d in desks)}, top {n}", "stock", "rebalance",
                    f"Chosen walk-forward from {evidence['variants']} desk combinations; {evidence['why']}",
                    tuple(sorted({"rule": "blend", "desks": list(desks), "n": int(n)}.items())), learned=True)


# ── the lab's strategies, stored in stocks-research.db ───────────────────────────────────────────
def strategies(research: StocksResearch) -> tuple[list[Strategy], set[str]]:
    """Every lab strategy ever promoted (so retired ones keep exiting) and the ids of the retired ones."""
    out, retired = [], set()
    for r in research.conn.execute("SELECT * FROM stk_lab_strategies ORDER BY created_at"):
        spec = json.loads(r["spec_json"])
        out.append(Strategy(spec["id"], spec["version"], spec["title"], spec["asset"], spec["style"], spec["hypothesis"],
                            tuple(sorted(spec["params"].items())), learned=True))
        if r["retired_at"]:
            retired.add(spec["id"])
    return out, retired


def promote(research: StocksResearch, st: Strategy, evidence: dict[str, Any], now: datetime) -> list[str]:
    """Add ``st`` (if new) and retire the oldest active lab strategies beyond the limit; returns the retired ids."""
    if research.conn.execute("SELECT 1 FROM stk_lab_strategies WHERE id = ?", (st.id,)).fetchone():
        return []
    research.conn.execute("INSERT INTO stk_lab_strategies(id, created_at, spec_json, spec_sha256, evidence_json)"
                          " VALUES (?, ?, ?, ?, ?)", (st.id, to_iso(now), canonical_json({**st.content(),
                                                                                           "title": st.title}),
                                                      st.spec_sha256, canonical_json(evidence)))
    active = [r["id"] for r in research.conn.execute(
        "SELECT id FROM stk_lab_strategies WHERE retired_at IS NULL ORDER BY created_at")]
    retire = active[:max(0, len(active) - MAX_ACTIVE)]
    for sid in retire:
        research.conn.execute("UPDATE stk_lab_strategies SET retired_at = ? WHERE id = ?", (to_iso(now), sid))
    return retire


def run(research: StocksResearch, panel: Panel, *, now: datetime, mode: str) -> dict[str, Any]:
    """Evaluate on the rebuilt months; with ``mode`` "paper", promote the current best combination if the walk-forward
    test passed. Every run is recorded (stk_runs, kind "lab")."""
    history = val.months(research)
    result: dict[str, Any] = (evaluate(val.Forward(panel), history) if history else
                              {"why": "No rebuilt months yet", "passed": False, "variants": len(variants()), "months": 0})
    result["mode"] = mode
    result["promoted"] = None
    result["retired"] = []
    if mode == "paper" and result.get("passed") and result.get("best_now"):
        st = as_strategy(str(result["best_now"]), result)
        result["retired"] = promote(research, st, {k: v for k, v in result.items() if k != "walk_forward"}, now)
        result["promoted"] = st.id
    research.record_run("lab", now, now, history[-1].asof if history else None, True,
                        {k: v for k, v in result.items() if k != "walk_forward"})
    return result


__all__ = ["LAB_DESKS", "MAX_ACTIVE", "MIN_OOS", "MIN_TRAIN", "Variant", "as_strategy", "committee_picks", "evaluate",
           "monthly_excess", "picks", "promote", "run", "strategies", "variants"]
