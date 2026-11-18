"""The Learner's shadow views (docs/STOCKS_LEARNER.md → What you see). Shadow means: shown beside the rules, labelled
as not traded and not yet proven, and read by nothing that trades, ranks or calibrates.

* **Learner's view** on each play of the current Plays board: the frozen model's chance of a profit at 1, 5, 10 and 21
  sessions and at expiry, its expected return on the money at risk held to expiry with a 10–90% range, and the exit
  rule it expects to do best. The play is valued exactly as the experience log values a candidate (its own contracts
  at the board session's quotes, the same features).
* **Learner's own top ideas (shadow)**: the candidates it would compose from the action grid, ranked by its expected
  return, from the newest recorded closing session (at most ``IDEAS_PER_STOCK`` a stock), each with its plain-words
  dials and legs.

``refresh`` writes ``stocks-learner-shadow.json`` (the web page reads it; a page load never computes anything) at most
every ``THROTTLE`` while the board changes, and appends each closed session's views to ``stk_learn_shadow`` once, so
they can be checked against what happened later. ``summary`` is the plain status: data volume, labels, the last
training and its held-out results.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from desk.core.clock import to_iso
from desk.core.paths import DeskPaths
from desk.stocks.connection import _atomic_private
from desk.stocks.learner import actions as act
from desk.stocks.learner import experience as ex
from desk.stocks.learner import features as ft
from desk.stocks.learner import labels as lb
from desk.stocks.learner import model as md

SAVED = "stocks-learner-shadow.json"
THROTTLE = timedelta(minutes=30)
STALE = timedelta(hours=6)          # an unchanged view is still rewritten this often (its data volume moves)
IDEAS = 10
IDEAS_PER_STOCK = 2
LABEL = "Shadow: the Learner's view. Not traded, not used by Plays, PAPER or Learning, and not yet proven."


def saved(home: Path) -> dict[str, Any] | None:
    try:
        path = home / SAVED
        if path.stat().st_size > 8_000_000:
            return None
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def model_summary(row: sqlite3.Row | Mapping[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    metrics = json.loads(row["metrics_json"])
    return {"id": row["id"], "trained_at": row["trained_at"], "seconds": row["seconds"], "peak_mb": row["peak_mb"],
            "bytes": row["bytes"], "data": json.loads(row["data_json"]), "code_version": row["code_version"],
            "held_out": {k: metrics.get(k) for k in ("folds", "test_rows", "test_days", "test_sources", "rank_correlation",
                                                    "rank_correlation_days", "daily_rank_correlation", "chosen_variant",
                                                    "variants", "deciles", "calibration", "quantile_coverage",
                                                    "best_exit", "blocks", "reason", "costs", "cpcv", "neutral",
                                                    "ensemble", "overfitting", "trained_on", "expected_model")},
            "importance": metrics.get("importance") or [], "evaluation": evaluation(metrics)}


def _signed(x: float | None, digits: int = 0) -> str:
    return "—" if x is None else f"{x * 100:+.{digits}f}%"


def _rho(x: float | None) -> str:
    return "—" if x is None else f"{x:+.2f}"


MEMBER_WORDS = {"gbt": "gradient-boosted trees", "ridge": "a ridge (L2 linear) model",
                "extratrees": "extra trees", "ensemble": "the ensemble"}


def evaluation(metrics: Mapping[str, Any], fills: Mapping[str, Any] | None = None) -> list[dict[str, str]]:
    """How strictly the model was tested, in plain words: each line a title, the sentence and a short hint. Shared by
    the Learner page and the Learning report."""
    out: list[dict[str, str]] = []
    costs = metrics.get("costs") or {}
    dec = metrics.get("deciles") or {}
    edge = dec.get("top_minus_random")
    if "costs" in metrics:
        if not costs.get("rows"):
            text = ("No held-out candidate has its return at expiry yet, so the expected fill has not been compared "
                    "with paying the ask on later days.")
        elif costs.get("limit_rows"):
            text = (f"On held-out candidates it would try a limit inside the spread {costs['limit_share']:.0%} of the "
                    f"time, with a {costs['mean_chance']:.0%} average chance of filling. Its top tenth returned "
                    f"{_signed((costs.get('top_net') or {}).get('mean'))} at the expected fill against "
                    f"{_signed((costs.get('top_at_ask') or {}).get('mean'))} paying the ask.")
        else:
            seen = (fills or {}).get("observations")
            text = ("No saved quotes yet show a limit inside the spread filling, so every trade is costed paying the "
                    "ask (the far side)" + (f"; {seen:,} fill observations so far." if seen else "."))
        out.append({"title": "Expected fill vs paying the ask", "text": text,
                    "hint": "A limit between the mid and the ask saves money only if it fills; a fill counts only "
                            "when a later saved quote that day crossed it."})
    if edge:
        out.append({"title": "Net-of-cost edge",
                    "text": f"Its top tenth of each held-out day beat that day's average candidate by "
                            f"{_signed(edge['mean'], 1)} (90% range {_signed(edge['low'], 1)} to "
                            f"{_signed(edge['high'], 1)}, {edge['days']} days), after fees and the expected fill.",
                    "hint": "Returns on the money at risk held to expiry, on days the model never trained on."})
    cp = metrics.get("cpcv") or {}
    if cp.get("splits"):
        ps = cp.get("path_sharpe") or {}
        out.append({"title": "Many held-out splits",
                    "text": f"In {cp['splits']} held-out splits, {cp['profitable']} were profitable and "
                            f"{cp['beat_average']} beat the day's average candidate"
                            + (f"; across {len(cp.get('paths') or [])} complete held-out histories the daily Sharpe "
                               f"ratio ran from {ps['min']:+.2f} to {ps['max']:+.2f}." if ps else "."),
                    "hint": "Purged combinatorial cross-validation: every pair of six time blocks is held out in "
                            "turn, with overlapping labels removed, so one lucky stretch cannot decide."})
    ov = metrics.get("overfitting") or {}
    dsr = ov.get("dsr")
    if ov and not dsr:
        out.append({"title": "After accounting for what it tried",
                    "text": f"No deflated Sharpe ratio yet (too few held-out days with results); when there is one it "
                            f"will be after accounting for the {ov.get('trials_total', 0):,} things it tried.",
                    "hint": "The chance the edge is real once every model variant ever tested is counted; more "
                            "tries raise the bar."})
    if dsr:
        out.append({"title": "After accounting for what it tried",
                    "text": f"Deflated Sharpe ratio {dsr['dsr']:.2f} after accounting for the {dsr['trials']:,} "
                            f"things it tried (it needs {ov.get('required_dsr', 0.95):.2f} to count): "
                            + ("the edge is unlikely to be luck." if dsr["passes"] else
                               "the edge could still be luck from trying many variants."),
                    "hint": "The chance the edge is real once every model variant ever tested is counted; more "
                            "tries raise the bar."})
    pbo = ov.get("pbo")
    if pbo:
        out.append({"title": "Probability of overfitting",
                    "text": f"{pbo['pbo']:.0%}: how often the variant that looked best on half of the days was in "
                            f"the bottom half on the other days ({pbo['splits']:,} ways of splitting "
                            f"{pbo['periods']} days among {pbo['trials']} variants).",
                    "hint": "Near 50% means picking the best variant was no better than chance; low is good."})
    nt = metrics.get("neutral") or {}
    if nt.get("ran") and not nt.get("rows"):
        out.append({"title": "Raw vs market-neutral skill",
                    "text": "Not measured yet: no held-out candidate has both its return and the market's over the "
                            "same days.", "hint": "Market-neutral skill is what is left once SPY's and the sector's "
                                                  "move over the same days is taken out."})
    elif nt.get("ran"):
        raw, neu = nt.get("raw_model") or {}, nt.get("neutral_model") or {}
        target = "the market-neutral target" if nt.get("chosen") == "neutral" else "the raw net return"
        out.append({"title": "Raw vs market-neutral skill",
                    "text": f"Rank correlation {_rho(raw.get('raw'))} raw and {_rho(raw.get('neutral'))} after "
                            f"removing the market's and the sector's direction; a model trained on the neutral "
                            f"return scored {_rho(neu.get('neutral'))} there. It trains on {target}.",
                    "hint": "A call bought before a rally wins whoever picked it: market-neutral skill is what is "
                            "left once SPY's and the sector's move over the same days is taken out."})
    team = metrics.get("ensemble") or {}
    if team.get("members") and all(v.get("rank_correlation") is None for v in team["members"].values()):
        out.append({"title": "Ensemble vs single model",
                    "text": "Not compared yet: no held-out results to score the models on, so the gradient-boosted "
                            "trees are used alone.",
                    "hint": "The ensemble is used only when it beats the best single model on later days."})
    elif team.get("members"):
        scores = ", ".join(f"{MEMBER_WORDS.get(k, k)} {_rho(v.get('rank_correlation'))}"
                           for k, v in team["members"].items())
        chosen = str(team.get("chosen"))
        out.append({"title": "Ensemble vs single model",
                    "text": f"Held out: {scores}; the ensemble {_rho((team.get('ensemble') or {}).get('rank_correlation'))}."
                            + (" It uses the ensemble, which beat every single model." if chosen == "ensemble" else
                               f" It uses {MEMBER_WORDS.get(chosen, chosen)} alone: the ensemble did not beat it."),
                    "hint": "Rank correlation between predicted and realised returns on later days; weights come "
                            "only from earlier days."})
    return out


def summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Data volume, label coverage and the last model (read-only)."""
    try:
        volume = ex.volume(conn)
        labels = lb.coverage(conn)
        row = md.latest(conn)
        models = conn.execute("SELECT COUNT(*) FROM stk_learn_models").fetchone()[0]
    except sqlite3.OperationalError:          # a database from before the Learner
        return {"volume": {}, "labels": {}, "model": None, "models": 0}
    from desk.stocks.learner import fills

    return {"volume": volume, "labels": labels, "model": model_summary(row), "models": int(models),
            "fills": fills.summary(conn)}


def _legs_view(legs: Sequence[Sequence[Any]]) -> list[dict[str, Any]]:
    from desk.stocks.play_tracking import parse_occ

    out = []
    for occ, sign in legs:
        _, kind, strike, expiration = parse_occ(occ)
        out.append({"occ": occ, "side": "buy" if sign > 0 else "sell", "kind": kind, "strike": strike,
                    "expiration": expiration.isoformat()})
    return out


def ideas(conn: sqlite3.Connection, fitted: md.Fitted) -> dict[str, Any]:
    """The model's top grid candidates of the newest recorded forward session."""
    row = conn.execute("SELECT MAX(session) FROM stk_learn_days WHERE source='forward' AND actions_version=?",
                       (act.version_hash(),)).fetchone()
    if not row or row[0] is None:
        return {"session": None, "items": []}
    session = int(row[0])
    items: list[dict[str, Any]] = []
    X: list[list[float]] = []
    for g in conn.execute("SELECT symbol, rows_zlib FROM stk_learn_candidates WHERE source='forward' AND session=? AND "
                          "actions_version=?", (session, act.version_hash())):
        group = ex.unpack(g["rows_zlib"])
        for r in group["rows"]:
            if not r["key"]:
                continue
            X.append(md.row_vector(group["stock"], group["market"], r["x"], False))
            items.append({"symbol": g["symbol"], "price": group["price"], **r})
    if not items:
        return {"session": date.fromordinal(session).isoformat(), "items": []}
    p = md.predict(fitted, np.asarray(X, dtype=np.float32))
    expected = p.get("expected")
    if expected is None:
        return {"session": date.fromordinal(session).isoformat(), "items": [], "reason": "no expected-return model yet"}
    order = np.argsort(-expected)
    out, per = [], Counter[str]()
    for i in order:
        it = items[int(i)]
        if per[it["symbol"]] >= IDEAS_PER_STOCK:
            continue
        per[it["symbol"]] += 1
        a = act.parse_key(it["key"])
        out.append({"symbol": it["symbol"], "structure": a.structure, "title": act.BY_STRUCTURE[a.structure].title,
                    "dials": a.describe(), "key": it["key"], "action": it["action"], "entry": it["entry"],
                    "max_loss": it["max_loss"], "max_gain": it["max_gain"],
                    "expiration": date.fromordinal(int(it["expiration"])).isoformat(), "legs": _legs_view(it["legs"]),
                    "explore": bool(it["explore"]), "origin": it["origin"], "learner": md.describe(p, int(i))})
        if len(out) >= IDEAS:
            break
    return {"session": date.fromordinal(session).isoformat(), "candidates": len(items), "items": out}


def board_views(market: Any, research: Any, board: Mapping[str, Any], fitted: md.Fitted,
                now: datetime) -> dict[str, Any]:
    """The model's view of each play on the board (main lists of every section), keyed by its legs."""
    from desk.stocks import plays as pl

    if not board.get("asof"):
        return {}
    session = date.fromisoformat(board["asof"])
    wanted: dict[str, list[tuple[str, list[dict[str, Any]]]]] = {}
    for c in board.get("categories") or []:
        for p in c.get("plays") or []:
            wanted.setdefault(p["symbol"], []).append((f"plays:{c['id']}#{p.get('rank')}", p["legs"]))
    if not wanted:
        return {}
    ic, months, ic_source = pl.committee_ic(research)
    ctx = pl.Context(session, now, pl._rate(market, session), ic, months, ic_source, {}, [])
    reader = ft.Reader(market.conn, research.conn, session)
    loaded = [u for u in pl.load(market, research, session) if u.symbol in wanted or u.symbol == "SPY"]
    tables = {u.symbol: tb for u in loaded if (tb := pl.table(u, ctx)) is not None}
    from desk.stocks.forecast import structure as st

    ctx.outlook = st.outlook(market.conn, research.conn, session, sorted(tables))
    market_x = ft.market_features(reader, tables["SPY"].u if "SPY" in tables else None, ctx.rate)
    rows, keys = [], []
    for symbol, picks in wanted.items():
        tb = tables.get(symbol)
        if tb is None:
            continue
        stock = ft.stock_features(reader, tb.u, ctx.rate)
        for origin, legs in picks:
            row = ex.pick_row(tb, ctx, legs, origin)
            if row is None:
                continue
            rows.append(md.row_vector(stock, market_x, row.x, False))
            keys.append(ex.legs_key((leg["occ"], leg["side"]) for leg in legs))
    if not rows:
        return {}
    p = md.predict(fitted, np.asarray(rows, dtype=np.float32))
    return {k: md.describe(p, i) for i, k in enumerate(keys)}


def refresh(paths: DeskPaths, dbs: Any, now: datetime, *, force: bool = False) -> dict[str, Any]:
    """Rewrite the saved shadow view when the board or the model changed (at most every ``THROTTLE``)."""
    from desk.stocks.repo import StocksMarket, StocksResearch
    from desk.stocks.runner import saved as saved_view

    research = StocksResearch(dbs.research)
    market = StocksMarket(dbs.market)
    status = summary(research.conn)
    row = md.latest(research.conn)
    previous = saved(paths.home) or {}
    try:
        board = saved_view(paths, "plays") or {}
    except Exception:  # an unreadable board: the views wait for the next one
        board = {}
    model_id = None if row is None else row["id"]
    stamp = {"board": board.get("input_hash"), "model": model_id}
    age = (now - datetime.fromisoformat(previous["generated_at"]) if previous.get("generated_at")
           else timedelta(days=999))
    unchanged = previous.get("stamp") == stamp and age < STALE
    recent = (previous.get("stamp") or {}).get("model") == model_id and age < THROTTLE
    if not force and (unchanged or recent):
        return {"refreshed": False}
    out: dict[str, Any] = {"label": LABEL, "generated_at": to_iso(now), "stamp": stamp, **status,
                           "plays": {}, "ideas": {"session": None, "items": []}, "asof": board.get("asof")}
    fitted = md.load_models(paths.home, row) if row is not None else None
    if row is not None and fitted is None:
        out["problem"] = "The newest model's file is missing or does not match its recorded hash; it is not used."
    if fitted is not None:
        out["plays"] = board_views(market, research, board, fitted, now)
        out["ideas"] = ideas(research.conn, fitted)
        record(research.conn, board, out, model_id or "", now)
    _atomic_private(paths.home / SAVED, json.dumps(out, allow_nan=False, default=str))
    return {"refreshed": True, "plays": len(out["plays"]), "ideas": len(out["ideas"].get("items") or [])}


def record(conn: sqlite3.Connection, board: Mapping[str, Any], out: Mapping[str, Any], model_id: str,
           now: datetime) -> None:
    """Each closed session's shadow views, once per model (append-only), for a later forward check."""
    if board.get("session_closed") and board.get("asof") and out.get("plays"):
        conn.execute("INSERT OR IGNORE INTO stk_learn_shadow(session, model_id, kind, payload_json, recorded_at) "
                     "VALUES (?,?,?,?,?)", (date.fromisoformat(board["asof"]).toordinal(), model_id, "plays",
                                            json.dumps(out["plays"], allow_nan=False), to_iso(now)))
    ideas_ = out.get("ideas") or {}
    if ideas_.get("session") and ideas_.get("items"):
        conn.execute("INSERT OR IGNORE INTO stk_learn_shadow(session, model_id, kind, payload_json, recorded_at) "
                     "VALUES (?,?,?,?,?)", (date.fromisoformat(ideas_["session"]).toordinal(), model_id, "ideas",
                                            json.dumps(ideas_["items"], allow_nan=False), to_iso(now)))


__all__ = ["IDEAS", "LABEL", "SAVED", "board_views", "evaluation", "ideas", "model_summary", "record", "refresh", "saved",
           "summary"]
