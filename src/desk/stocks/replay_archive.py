"""Immutable daily replay views, separate from the forward ledger and the current market database."""

from __future__ import annotations

import json
import re
import sqlite3
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any
from uuid import uuid4

from desk.core.errors import DeskError
from desk.core.paths import DeskPaths
from desk.stocks.backtest import position_view
from desk.stocks.paper.book import MICRO, PaperBook
from desk.storage.db import DbKind, connect, migrate, transaction


def create(paths: DeskPaths) -> tuple[str, sqlite3.Connection]:
    run_id = uuid4().hex
    path = paths.db_dir / "stocks-replays" / (run_id + ".db")
    conn = connect(path, DbKind.STOCKS_LEDGER)
    migrate(conn, DbKind.STOCKS_LEDGER)
    conn.execute("CREATE TABLE replay_sessions(day INTEGER,account TEXT,payload BLOB,PRIMARY KEY(day,account))")
    conn.execute("CREATE TABLE replay_meta(key TEXT PRIMARY KEY,value TEXT)")
    conn.execute("INSERT INTO replay_meta VALUES ('complete','false')")
    return run_id, conn


def capture(conn: sqlite3.Connection, day: date, drawdowns: dict[str, tuple[float, float]]) -> None:
    book = PaperBook(conn)
    positions: dict[str, list[dict[str, Any]]] = {}
    for p in book.positions():
        reason = conn.execute("SELECT reasons FROM stk_decisions WHERE id=?", (p.decision_id,)).fetchone()
        positions.setdefault(p.account, []).append({**position_view(p), "entry_reason": reason[0] if reason else None})
    with transaction(conn):
        for account in book.accounts():
            equity_row = conn.execute("SELECT * FROM stk_equity WHERE day=? AND account=?", (day.toordinal(), account)).fetchone()
            if equity_row is None:
                continue
            equity = equity_row["equity_micros"] / MICRO
            peak, dd = drawdowns.get(account, (equity, 0.0))
            peak = max(peak, equity)
            dd = max(dd, 1 - equity / peak if peak > 0 else 0)
            drawdowns[account] = peak, dd
            closed = conn.execute(
                "SELECT COUNT(*),COALESCE(SUM(exit_credit_micros-exit_fees_micros-entry_cost_micros-entry_fees_micros),0),"
                "SUM(CASE WHEN exit_credit_micros-exit_fees_micros>entry_cost_micros+entry_fees_micros THEN 1 ELSE 0 END) "
                "FROM stk_positions WHERE account=? AND state='CLOSED'",
                (account,),
            ).fetchone()
            actions = [
                dict(r)
                for r in conn.execute(
                    "SELECT action,COUNT(*) count FROM stk_decisions WHERE account=? AND asof=? GROUP BY action",
                    (account, day.toordinal()),
                )
            ]
            decisions = [d for d in book.decisions(account, 100) if d["asof"] == day.isoformat()]
            pending = [
                {"id": o.id, "account": account, "fill_after": o.fill_after.isoformat(), "order": o.order}
                for o in book.pending(account)
            ]
            start = book.deposits(account)
            state = {
                "day": day.isoformat(),
                "account": account,
                "equity": equity,
                "cash": equity_row["cash_micros"] / MICRO,
                "deposits": start,
                "return": equity / start - 1 if start else None,
                "max_drawdown": dd,
                "realized_pnl": closed[1] / MICRO,
                "unrealized_pnl": equity - start - closed[1] / MICRO,
                "trades": closed[0],
                "win_rate": closed[2] / closed[0] if closed[0] else None,
                "open_positions": len(positions.get(account, [])),
                "positions": positions.get(account, []),
                "opened_today": conn.execute(
                    "SELECT COUNT(*) FROM stk_positions WHERE account=? AND entry_day=?", (account, day.toordinal())
                ).fetchone()[0],
                "closed_today": conn.execute(
                    "SELECT COUNT(*) FROM stk_positions WHERE account=? AND exit_day=?", (account, day.toordinal())
                ).fetchone()[0],
                "decisions": decisions,
                "decision_counts": actions,
                "pending": pending,
            }
            conn.execute(
                "INSERT INTO replay_sessions VALUES (?,?,?)",
                (day.toordinal(), account, zlib.compress(json.dumps(state, allow_nan=False).encode())),
            )


@contextmanager
def reader(paths: DeskPaths, run_id: str) -> Iterator[sqlite3.Connection]:
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise DeskError("Choose a completed replay from the Paper page")
    path = paths.db_dir / "stocks-replays" / (run_id + ".db")
    if not path.is_file() or path.is_symlink():
        raise DeskError("This replay archive is unavailable. Run a new replay on the Paper page.")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
    conn.row_factory = sqlite3.Row
    try:
        complete = conn.execute("SELECT value FROM replay_meta WHERE key='complete'").fetchone()
        if not complete or complete[0] != "true":
            raise DeskError("Replay is still being computed; the previous completed run remains available")
        yield conn
    finally:
        conn.close()


def session(
    paths: DeskPaths, run_id: str, account: str, day: date, offset: int = 0, closed: bool = False, symbol: str = ""
) -> dict[str, Any]:
    with reader(paths, run_id) as conn:
        row = conn.execute("SELECT payload FROM replay_sessions WHERE account=? AND day=?", (account, day.toordinal())).fetchone()
        if row is None:
            raise DeskError("That session was not processed for this strategy; choose another saved date")
        data: dict[str, Any] = json.loads(zlib.decompress(row[0]))
        if closed:
            rows = conn.execute(
                "SELECT id FROM stk_positions WHERE account=? AND state='CLOSED' AND exit_day<=? AND symbol LIKE ? "
                "ORDER BY closed_at DESC,id DESC LIMIT 12 OFFSET ?",
                (account, day.toordinal(), symbol + "%", offset),
            )
            ids = {r[0] for r in rows}
            # SQL paging retains all trades, including histories beyond the compact report's detail limit.
            values = []
            for ident in ids:
                from desk.stocks.paper.book import _position

                p = _position(conn.execute("SELECT * FROM stk_positions WHERE id=?", (ident,)).fetchone())
                reason = conn.execute("SELECT reasons FROM stk_decisions WHERE id=?", (p.decision_id,)).fetchone()
                values.append({**position_view(p), "entry_reason": reason[0] if reason else None})
            data["visible_trades"] = sorted(values, key=lambda p: (p["exit_day"], p["id"]), reverse=True)
            data["total"] = conn.execute(
                "SELECT COUNT(*) FROM stk_positions WHERE account=? AND state='CLOSED' AND exit_day<=? AND symbol LIKE ?",
                (account, day.toordinal(), symbol + "%"),
            ).fetchone()[0]
        else:
            values = [p for p in data["positions"] if p["symbol"].startswith(symbol)]
            data.update(visible_trades=values[offset : offset + 12], total=len(values))
        return data
