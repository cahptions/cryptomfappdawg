"""The Learner's place in the collection pass (``runner.publish_plays``): after Plays records a closed session, record
that session's candidates; then write every label that has come due and extend the replay examples, each bounded so a
pass stays short. Never stops collection: a failure is recorded on the System page (job ``learner``)."""

from __future__ import annotations

import sqlite3
from contextlib import suppress
from datetime import date, datetime
from typing import Any

from desk.core.paths import DeskPaths
from desk.stocks.learner import actions as act
from desk.stocks.learner import experience as ex
from desk.stocks.learner import features as ft
from desk.stocks.learner import labels as lb
from desk.stocks.repo import StocksDatabases, StocksMarket, StocksResearch

JOB = "learner"


def register_specs(market: StocksMarket, now: datetime) -> dict[str, str]:
    """Freeze the Learner's rule sets (a changed rule without a new version is refused, as every frozen rule set)."""
    from desk.stocks.learner import model

    return {act.NAME: market.register_spec(act.NAME, act.VERSION, act.content(), now),
            ft.NAME: market.register_spec(ft.NAME, ft.VERSION, ft.content(), now),
            lb.NAME: market.register_spec(lb.NAME, lb.VERSION, lb.content(), now),
            model.NAME: market.register_spec(model.NAME, model.VERSION, model.content(), now)}


def _learning_book(paths: DeskPaths) -> sqlite3.Connection | None:
    from desk.stocks.learning import FILE

    path = paths.db_dir / FILE
    if not path.exists():
        return None
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    conn.execute("PRAGMA query_only=ON")
    return conn


def after_plays(paths: DeskPaths, dbs: StocksDatabases, plays: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Record the closed session Plays just built (once), then label and replay within this pass's budgets."""
    market, research = StocksMarket(dbs.market), StocksResearch(dbs.research)
    register_specs(market, now)
    out: dict[str, Any] = {}
    if plays.get("session_closed") and plays.get("asof"):
        session = date.fromisoformat(plays["asof"])
        if not ex.recorded(research.conn, "forward", session):
            book = _learning_book(paths)
            try:
                books = [("paper", dbs.ledger), *([("learning", book)] if book is not None else [])]
                out["recorded"] = ex.record_forward(market, research, session, now, books=books)
            finally:
                if book is not None:
                    book.close()
    out["labels"] = lb.run(research.conn, market.conn, lb.ForwardPrices(market.conn, research.conn), now)
    from desk.stocks.learner import replay

    out["replay"] = replay.step(market, research, now)
    out["replay_labels"] = lb.run(research.conn, market.conn, lb.ReplayPrices(market.conn), now)
    from desk.stocks.learner import view

    out["shadow"] = view.refresh(paths, dbs, now)
    return out


def run(paths: DeskPaths, dbs: StocksDatabases, plays: dict[str, Any], now: datetime) -> dict[str, Any]:
    """``after_plays`` guarded: the outcome is recorded as the ``learner`` job and never raised."""
    market = StocksMarket(dbs.market)
    try:
        detail = after_plays(paths, dbs, plays, now)
        market.job(JOB, now, detail=detail)
        return {"ok": True, **detail}
    except Exception as exc:  # recorded on the System page; collection continues
        with suppress(Exception):
            market.job(JOB, now, error=f"{type(exc).__name__}: {str(exc)[:240]}")
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:240]}"}


__all__ = ["JOB", "after_plays", "register_specs", "run"]
