"""Running the learning lab inside the Robinhood desk: history, monthly runs, records and the forward check.

``desk.rh.learn`` is the pure lab. This module feeds it stored bars and measured costs, records each run
(append-only), decides when the next run is due (once a month, the same step the lab is judged on), and checks
the learned strategy's own PAPER results so a choice that stops working is benched until the next run.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import numpy as np

from desk.core.clock import parse_iso8601, to_iso
from desk.core.config import LoadedConfig
from desk.core.errors import DeskError
from desk.core.hashing import canonical_json
from desk.rh import learn
from desk.rh.coinbase import CoinbaseClient
from desk.rh.history import DAILY, load_daily, today_start, universe
from desk.rh.repo import RhLedger, RhMarket
from desk.stats.core import stationary_bootstrap_means

FORWARD_MIN_TRADES = 20
FORWARD_CONFIDENCE = 0.90


async def backfill_daily(coinbase: CoinbaseClient, market: RhMarket, symbols: list[str], now: datetime, *,
                         days: int = learn.HISTORY_DAYS) -> dict[str, Any]:
    """Fetch daily bars older than the oldest stored one, back to ``days`` ago (the regular sync only moves
    forward). Missing products and errors are reported, never filled."""
    report: dict[str, Any] = {"added": 0, "symbols": 0, "missing_products": [], "errors": {}}
    end_limit = today_start(now)
    begin = end_limit - timedelta(days=days)
    for symbol in symbols:
        row = market.conn.execute("SELECT MIN(start_ms) FROM candles WHERE source = 'coinbase' AND product = ? AND"
                                  " granularity = ?", (symbol, DAILY)).fetchone()
        oldest = None if row[0] is None else datetime.fromtimestamp(row[0] / 1000, begin.tzinfo)
        until = oldest if oldest is not None else end_limit
        if until - begin <= timedelta(days=7):
            continue
        try:
            bars = await coinbase.candles(symbol, DAILY, begin, until)
        except DeskError as exc:
            key = "missing_products" if "no product" in str(exc) else "errors"
            if key == "missing_products":
                report[key].append(symbol)
            else:
                report[key][symbol] = str(exc)[:160]
            continue
        report["added"] += market.save_candles("coinbase", bars, now)
        report["symbols"] += 1
    return report


def costs(market: RhMarket, symbols: list[str], assumed: Decimal) -> tuple[np.ndarray, dict[str, str]]:
    """Each coin's measured Robinhood round trip (median of recent probes) or the configured assumption."""
    values, sources = [], {}
    for symbol in symbols:
        measured = market.measured_roundtrip(symbol)
        if measured is not None and measured >= 0:
            values.append(float(measured))
            sources[symbol] = f"{float(measured):.4f} measured"
        else:
            values.append(float(assumed))
            sources[symbol] = f"{float(assumed):.4f} assumed"
    return np.array(values), sources


@dataclass(frozen=True)
class Run:
    id: int
    created_at: datetime
    data_end: date
    lab_sha256: str
    selection_sha256: str
    promoted: str | None
    report: dict[str, Any]


def latest(conn: sqlite3.Connection) -> Run | None:
    row = conn.execute("SELECT * FROM rh_learning_runs ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return None
    return Run(int(row["id"]), parse_iso8601(row["created_at"]), date.fromisoformat(row["data_end"]),
               row["lab_sha256"], row["selection_sha256"], row["promoted"], json.loads(row["report_json"]))


def history(conn: sqlite3.Connection, limit: int = 24) -> list[dict[str, Any]]:
    return [{"id": r["id"], "created_at": r["created_at"], "data_end": r["data_end"], "promoted": r["promoted"],
             "lab_sha256": r["lab_sha256"][:12]}
            for r in conn.execute("SELECT id, created_at, data_end, promoted, lab_sha256 FROM rh_learning_runs"
                                  " ORDER BY id DESC LIMIT ?", (limit,))]


def due(conn: sqlite3.Connection, now: datetime) -> bool:
    """A new run is due when none exists, the lab changed, or a month of new data has arrived since the last."""
    run = latest(conn)
    if run is None or run.lab_sha256 != learn.lab_sha256():
        return True
    if now - run.created_at < timedelta(days=1):          # stale history must not cause a run every hour
        return False
    return (now.date() - timedelta(days=1)) - run.data_end >= timedelta(days=learn.TEST_DAYS)


def next_due(conn: sqlite3.Connection) -> date | None:
    run = latest(conn)
    return None if run is None else run.data_end + timedelta(days=learn.TEST_DAYS + 1)


def run_and_record(market: RhMarket, loaded: LoadedConfig, now: datetime) -> Run:
    """Learn from every closed daily bar up to yesterday and record the run (its own transaction)."""
    symbols, _ = universe(market)
    yesterday = now.date() - timedelta(days=1)
    panel = learn.build_panel(load_daily(market, symbols), until=yesterday)
    cost, sources = costs(market, panel.symbols, loaded.settings.robinhood.backtest_roundtrip_cost_fraction)
    settings = loaded.settings.robinhood
    result = learn.run_lab(panel, cost, now=now, cost_sources=sources,
                           reference_capital_usd=float(settings.starting_capital_usd),
                           ticket_fraction=float(min(settings.fixed_fraction, settings.max_ticket_fraction)))
    report = result.as_json()
    report["lab"] = {"version": learn.LAB_VERSION, "sha256": learn.lab_sha256()}
    selection = learn.selection_sha256(result)
    with market.conn:
        cursor = market.conn.execute(
            "INSERT INTO rh_learning_runs(created_at, data_end, lab_sha256, selection_sha256, promoted, report_json)"
            " VALUES (?, ?, ?, ?, ?, ?)", (to_iso(now), result.data_end, learn.lab_sha256(), selection, result.promoted,
                                           canonical_json(report)))
    return Run(int(cursor.lastrowid or 0), now, date.fromisoformat(result.data_end), learn.lab_sha256(), selection,
               result.promoted, report)


def forward(ledger: RhLedger, selection_sha256: str | None = None) -> dict[str, Any]:
    """The learned strategy's own PAPER record; benched when its upper confidence bound on the mean is below zero."""
    closed = [p for p in ledger.positions("CLOSED") if p.strategy_id == learn.STRATEGY_ID]
    current = [p for p in closed if selection_sha256 is None or p.spec_sha256 == selection_sha256]
    nets = [float((p.exit_credit or Decimal(0)) / p.entry_cost - 1) for p in current if p.entry_cost > 0]
    upper = None
    if len(nets) >= FORWARD_MIN_TRADES:
        means = stationary_bootstrap_means(np.array(nets), resamples=2000, seed=0)
        upper = float(np.quantile(means, FORWARD_CONFIDENCE))
    open_ = [p for p in ledger.positions("OPEN") if p.strategy_id == learn.STRATEGY_ID]
    return {"closed_all": len(closed), "closed_current": len(current), "open": len(open_),
            "wins": sum(1 for n in nets if n > 0), "mean_net": float(np.mean(nets)) if nets else None,
            "upper_bound": upper, "benched": upper is not None and upper < 0,
            "realized": str(sum((p.realized_pnl or Decimal(0) for p in closed), Decimal(0)))}


def describe_selection(run: Run) -> list[dict[str, Any]]:
    """Today's choice in words: the rules (or the model) the promoted procedure would trade."""
    selection = run.report.get("selection", {})
    out: list[dict[str, Any]] = []
    for key, label in (("best", "LEARN_BEST"), ("top3", "LEARN_TOP3")):
        for cid in selection.get(key, []):
            try:
                cand = learn.candidate_by_id(cid)
            except KeyError:
                continue
            out.append({"procedure": label, "id": cid, "describe": cand.describe()})
    model = selection.get("model")
    if model:
        out.append({"procedure": "LEARN_MODEL", "id": "MODEL", "describe":
                    f"Logistic model on {len(learn.MODEL_FEATURES)} daily features, fitted on {model['rows']} "
                    f"coin-days; each Monday buys up to {learn.MODEL_TOP} coins it rates ≥ {learn.MODEL_THRESHOLD:.0%} "
                    f"likely to gain after costs over {learn.MODEL_HOLD} days."})
    return out



__all__ = ["FORWARD_MIN_TRADES", "Run", "backfill_daily", "costs", "describe_selection", "due", "forward", "history",
           "latest", "next_due", "run_and_record"]
