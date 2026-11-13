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
                                                    "best_exit", "blocks", "reason")},
            "importance": metrics.get("importance") or []}


def summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Data volume, label coverage and the last model (read-only)."""
    try:
        volume = ex.volume(conn)
        labels = lb.coverage(conn)
        row = md.latest(conn)
        models = conn.execute("SELECT COUNT(*) FROM stk_learn_models").fetchone()[0]
    except sqlite3.OperationalError:          # a database from before the Learner
        return {"volume": {}, "labels": {}, "model": None, "models": 0}
    return {"volume": volume, "labels": labels, "model": model_summary(row), "models": int(models)}


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


__all__ = ["IDEAS", "LABEL", "SAVED", "board_views", "ideas", "model_summary", "record", "refresh", "saved", "summary"]
