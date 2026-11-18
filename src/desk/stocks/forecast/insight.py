"""The "Direction vs volatility" insight for the Learning report and the Learner page: which direction horizons are
live and how they scored against the baseline, the volatility-surprise model's skill (held out, and on the Learner's
delta-hedged labels), how often each part drove the top plays, and whether the structure pricing ranks any section."""

from __future__ import annotations

import sqlite3
from typing import Any

import numpy as np

from desk.stocks.forecast import direction as dr
from desk.stocks.forecast import store
from desk.stocks.forecast import structure as st
from desk.stocks.forecast import vol_surprise as vs

DH_GROUPS = 600                      # newest labelled stock-sessions read for the delta-hedged check


def hedged_check(research: sqlite3.Connection) -> dict[str, Any]:
    """Rank correlation between the predicted volatility surprise (signed by the position's vega: a long-volatility
    position gains when realized beats implied) and the realized delta-hedged return, on labelled candidates that
    carried the prediction."""
    from desk.stocks.learner import actions as act
    from desk.stocks.learner import labels as lb
    from desk.stocks.learner.experience import unpack

    try:
        groups = research.execute(
            "SELECT l.labels_zlib, c.rows_zlib FROM stk_learn_labels l JOIN stk_learn_candidates c ON "
            "c.source=l.source AND c.session=l.session AND c.symbol=l.symbol AND c.actions_version=l.actions_version "
            "WHERE l.labels_version=? AND l.actions_version=? AND l.checkpoint LIKE 'exp:%' ORDER BY l.session DESC "
            "LIMIT ?", (lb.version_hash(), act.version_hash(), DH_GROUPS)).fetchall()
    except sqlite3.OperationalError:
        return {"rows": 0}
    pred, real = [], []
    for lblob, cblob in groups:
        rows = unpack(cblob)["rows"]
        for r in lb.unpack_labels(lblob):
            if len(r) < 8 or r[7] is None or r[0] >= len(rows):
                continue
            x = rows[r[0]]["x"]
            surprise, vega = x.get("vol_surprise"), x.get("net_vega")
            if surprise is None or not vega:
                continue
            pred.append(float(surprise) * (1 if vega > 0 else -1))
            real.append(float(r[7]))
    if len(pred) < 30:
        return {"rows": len(pred)}
    from scipy.stats import spearmanr

    rho = spearmanr(pred, real).statistic
    return {"rows": len(pred), "rank_correlation": None if not np.isfinite(rho) else round(float(rho), 4)}


def summary(market: sqlite3.Connection | None, research: sqlite3.Connection | None) -> dict[str, Any] | None:
    """None before either model has a fit."""
    if market is None:
        return None
    d = store.latest(market, dr.MODEL, dr.VERSION)
    v = store.latest(market, vs.MODEL, vs.VERSION)
    if d is None and v is None:
        return None
    out: dict[str, Any] = {"direction": None, "vol_surprise": None}
    if d is not None:
        out["direction"] = {"fit_day": d.fit_day.isoformat(), "rows": d.rows, "stocks": d.stocks,
                            "seconds": d.seconds, "peak_mb": d.peak_mb,
                            "live": [h for h in dr.HORIZONS if d.live.get(h)],
                            "horizons": {h: {k: e.get(k) for k in ("rows", "sessions", "pinball", "pinball_baseline",
                                                                    "improvement", "t", "calibration_error",
                                                                    "calibration_error_baseline", "neutral_ic",
                                                                    "live", "note")}
                                         for h, e in d.evaluation.items()}}
    if v is not None:
        out["vol_surprise"] = {"fit_day": v.fit_day.isoformat(), "rows": v.rows, "stocks": v.stocks,
                               "seconds": v.seconds, "live": [h for h in vs.HORIZONS if v.live.get(h)],
                               "horizons": {h: {k: e.get(k) for k in ("rows", "sessions", "mse", "mse_har", "skill",
                                                                       "t", "live", "note")}
                                            for h, e in v.evaluation.items()}}
    if research is not None:
        out["drivers"] = st.driver_counts(research)
        out["gates"] = st.gates(research)
        out["calibration"] = st.calibration(research)
        out["hedged"] = hedged_check(research)
    return out


def sentences(info: dict[str, Any]) -> tuple[str, str]:
    """(headline, detail) in plain words."""
    parts = []
    d = info.get("direction")
    if d:
        live = d["live"]
        scored = [f"{h} {'session' if h == '1' else 'sessions'}: pinball {e['pinball']:.3f} vs {e['pinball_baseline']:.3f}"
                  f", calibration error {e['calibration_error']:.3f} vs {e['calibration_error_baseline']:.3f}"
                  for h, e in d["horizons"].items() if e.get("rows")]
        parts.append(f"Direction model (fit of {d['fit_day']}, {d['rows']:,} rows, {d['stocks']} stocks): "
                     + (f"live at {', '.join(map(str, live))} sessions" if live else "no horizon beats the "
                        "no-skill baseline yet, so KESTREL stays drift-neutral")
                     + (f". Held out against the baseline (lower is better): {'; '.join(scored)}" if scored else "")
                     + ".")
    v = info.get("vol_surprise")
    if v:
        skills = [f"{h}: {100 * e['skill']:+.1f}%" for h, e in v["horizons"].items() if e.get("skill") is not None]
        parts.append("Volatility surprise: " + (f"live at {', '.join(map(str, v['live']))} sessions"
                                                if v["live"] else "not beating HAR-IV alone yet")
                     + (f" (error cut vs HAR-IV, held out: {', '.join(skills)})" if skills else "") + ".")
    h = info.get("hedged") or {}
    if h.get("rank_correlation") is not None:
        parts.append(f"On {h['rows']:,} delta-hedged results its prediction ranked them with correlation "
                     f"{h['rank_correlation']:+.2f}.")
    drivers = info.get("drivers") or {}
    n = sum(drivers.get(k, 0) for k in ("direction", "volatility", "both"))
    if n:
        parts.append(f"Of {n} top-3 plays with an edge, {drivers.get('volatility', 0)} came mostly from the option "
                     f"price vs the expected movement, {drivers.get('direction', 0)} mostly from the direction and "
                     f"{drivers.get('both', 0)} from both.")
    ranked = sorted(k for k, g in (info.get("gates") or {}).items() if g.get("live"))
    parts.append(f"The structure edge ranks {', '.join(ranked)}." if ranked else
                 "The structure edge is recorded in shadow; no section's ranking uses it yet.")
    live_d = (d or {}).get("live") or []
    live_v = (v or {}).get("live") or []
    headline = ("Direction vs volatility: "
                + (f"direction live at {len(live_d)} of {len(dr.HORIZONS)} horizons" if live_d else
                   "direction at the no-skill baseline")
                + ", " + (f"volatility surprise live at {len(live_v)} of {len(vs.HORIZONS)}" if live_v else
                          "volatility from HAR-IV alone") + ".")
    return headline, " ".join(parts)


__all__ = ["hedged_check", "sentences", "summary"]
